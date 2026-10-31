"""User-scoped memory and durable background extraction."""
import json
import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy.engine import make_url
from core.config import settings

logger = logging.getLogger(__name__)


def _uri():
    url = make_url(settings.database_url)
    if url.get_backend_name() != 'postgresql':
        raise RuntimeError('长期记忆需要 PostgreSQL')
    return url.set(drivername='postgresql').render_as_string(hide_password=False)


def _namespace(user_id):
    return ('users', str(int(user_id)), 'memory_v2')


def _index():
    from langchain_core.embeddings import Embeddings

    class LocalEmbeddings(Embeddings):
        def embed_documents(self, texts):
            from agent.tools.search_regulations.scripts.index_ops.indexing import _get_encoder
            return _get_encoder().encode(texts, normalize_embeddings=True).tolist()

        def embed_query(self, text):
            return self.embed_documents([text])[0]

    return {'dims': 1024, 'embed': LocalEmbeddings(), 'fields': ['text']}


def setup():
    from langgraph.store.postgres import PostgresStore
    with PostgresStore.from_conn_string(_uri(), index=_index()) as store:
        store.setup()


def _items(store, user_id):
    result, offset = [], 0
    while True:
        page = store.search(_namespace(user_id), limit=100, offset=offset)
        result.extend(page)
        if len(page) < 100:
            return result
        offset += len(page)


def list_memories(*, runtime_context, offset=0):
    from langgraph.store.postgres import PostgresStore
    with PostgresStore.from_conn_string(_uri()) as store:
        items = store.search(_namespace(runtime_context.user_id), limit=51, offset=max(0, offset))
    return {'status': 'success', 'memories': [
        {'id': item.key, 'text': item.value.get('text', '')} for item in items[:50]],
        'has_more': len(items) > 50, 'next_offset': max(0, offset) + 50}


def forget_memories(*, runtime_context, ids=None, all_memories=False):
    import psycopg
    from langgraph.store.postgres import PostgresStore
    user_id = runtime_context.user_id
    if not all_memories and not ids:
        return {'status': 'error', 'error': {'code': 'missing_ids', 'detail': '请先确定需要删除的记忆'}}
    with psycopg.connect(_uri(), autocommit=False) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute('INSERT INTO memory_owners(user_id, revision, epoch, maintenance_due) VALUES (%s,0,0,%s) ON CONFLICT(user_id) DO NOTHING', (user_id, datetime.utcnow() + timedelta(days=30)))
                cur.execute('SELECT epoch FROM memory_owners WHERE user_id=%s FOR UPDATE', (user_id,))
                cur.fetchone()
                store = PostgresStore(conn)
                namespace = _namespace(user_id)
                keys = [item.key for item in _items(store, user_id)] if all_memories else list(dict.fromkeys(ids))
                existing = [key for key in keys if store.get(namespace, key)]
                if not all_memories and len(existing) != len(keys):
                    return {'status': 'error', 'error': {'code': 'not_found', 'detail': '部分记忆不存在，请重新查看'}}
                cur.execute('UPDATE memory_owners SET epoch=epoch+1, revision=revision+1 WHERE user_id=%s', (user_id,))
                cur.execute("UPDATE memory_jobs SET status='cancelled', updated_at=%s WHERE user_id=%s AND status IN ('pending','processing')", (datetime.utcnow(), user_id))
                for key in existing:
                    store.delete(namespace, key)
    return {'status': 'success', 'deleted': len(existing),
            'detail': '已删除长期记忆，原始聊天和会话摘要仍可能包含这些内容。'}


def _relevant(store, user_id, query):
    namespace = _namespace(user_id)
    if not query or not store.search(namespace, limit=1):
        return []
    return store.search(namespace, query=query, limit=settings.memory_recall_limit)


def read(user_id, query=''):
    from langgraph.store.postgres import PostgresStore
    from agent.runtime.history_memory import _tokens
    with PostgresStore.from_conn_string(_uri(), index=_index()) as store:
        items = _relevant(store, user_id, query)
    lines, used = [], 0
    for item in items:
        text = str(item.value.get('text') or '').strip()
        size = _tokens(text)
        if text and used + size <= settings.memory_recall_token_budget:
            lines.append('- ' + text)
            used += size
    return '\n'.join(lines)


def enqueue(db, *, user_id, conversation_id, user_message_id, assistant_message_id):
    from database.db import MemoryControl, MemoryJob, MemoryOwner
    from sqlalchemy.dialects.postgresql import insert
    db.execute(insert(MemoryOwner).values(user_id=user_id, epoch=0, revision=0,
                                         maintenance_due=datetime.utcnow() + timedelta(days=30))
               .on_conflict_do_nothing(index_elements=['user_id']))
    owner = db.query(MemoryOwner).filter_by(user_id=user_id).with_for_update().one()
    control = db.get(MemoryControl, 1)
    if control is None:
        raise RuntimeError('新记忆系统尚未初始化')
    db.add(MemoryJob(user_id=user_id, conversation_id=conversation_id,
                     user_message_id=user_message_id, assistant_message_id=assistant_message_id,
                     epoch=owner.epoch, generation=control.generation))


_TOOL = [{'type': 'function', 'function': {
    'name': 'update_memories',
    'description': '根据用户自己的陈述增删改跨会话记忆；无需要时不调用。不得保存密码、验证码、令牌等认证信息。',
    'parameters': {'type': 'object', 'properties': {
        'changes': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'action': {'type': 'string', 'enum': ['add', 'update', 'delete']},
            'id': {'type': 'string', 'description': '修改或删除时填写现有记忆 ID'},
            'text': {'type': 'string', 'description': '新增或修改后的记忆'},
        }, 'required': ['action']}}}, 'required': ['changes']}}}]


def _claim():
    from sqlalchemy import or_, exists, and_
    from sqlalchemy.orm import aliased
    from database.db import SessionLocal, MemoryJob
    now = datetime.utcnow()
    with SessionLocal() as db:
        earlier = aliased(MemoryJob)
        row = (db.query(MemoryJob).filter(
            or_(and_(MemoryJob.status == 'pending', or_(MemoryJob.next_attempt_at.is_(None), MemoryJob.next_attempt_at <= now)),
                and_(MemoryJob.status == 'processing', MemoryJob.lease_until < now)),
            ~exists().where(and_(earlier.user_id == MemoryJob.user_id, earlier.id < MemoryJob.id,
                                 earlier.status.in_(['pending', 'processing']))))
            .order_by(MemoryJob.id).with_for_update(skip_locked=True).first())
        if not row:
            db.commit()
            return None
        row.status = 'processing'
        row.attempts += 1
        row.lease_token = uuid.uuid4().hex
        row.lease_until = now + timedelta(seconds=settings.memory_job_lease_seconds)
        result = (row.id, row.lease_token)
        db.commit()
        return result


def _process(job_id, token):
    from database.db import SessionLocal, MemoryControl, MemoryJob, Message
    from langgraph.store.postgres import PostgresStore
    from llm.llm_api_client import complete_sync
    from agent.runtime.history_memory import _tokens
    with SessionLocal() as db:
        job = db.get(MemoryJob, job_id)
        if not job or job.lease_token != token:
            return
        user_id, message_id = job.user_id, job.user_message_id
        message = db.get(Message, message_id)
        if message is None:
            return
        question = message.content
        control = db.get(MemoryControl, 1)
        if not control or message.conversation_id != job.conversation_id:
            raise ValueError('记忆任务的会话或清理边界无效')
        previous = (db.query(Message).filter(
            Message.conversation_id == job.conversation_id,
            Message.id > control.cutoff_message_id, Message.id < message.id)
            .order_by(Message.id.desc()).yield_per(32))
        context, used = [], 0
        for row in previous:
            size = _tokens(row.content)
            if used + size > 4096:
                break
            context.append({'role': row.role, 'content': row.content})
            used += size
        context.reverse()
    with PostgresStore.from_conn_string(_uri(), index=_index()) as store:
        existing = [{'id': item.key, 'text': item.value.get('text', '')}
                    for item in _relevant(store, user_id, question)]
    result = complete_sync([
        {'role': 'system', 'content': '仅依据用户本人本轮表达，判断哪些信息值得跨会话保留，并合并或更新已有记忆；上下文仅用于理解本轮指代，不要单独从上下文或助手回答推断用户事实。用户要求忘记的信息不能再次保存。密码、验证码、密钥及会话令牌不可保存。不需要变更时不要调用工具。'},
        {'role': 'user', 'content': json.dumps({'现有记忆': existing, '近期上下文': context, '用户本轮消息': question}, ensure_ascii=False)},
    ], task='planning', tools=_TOOL)
    changes = []
    for call in result.get('calls') or []:
        if call.get('name') == 'update_memories':
            changes.extend(call.get('arguments', {}).get('changes') or [])
    _apply(job_id, token, changes)


def _apply(job_id, token, changes):
    import psycopg
    from langgraph.store.postgres import PostgresStore
    now = datetime.utcnow()
    with psycopg.connect(_uri(), autocommit=False) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute('SELECT user_id, epoch, generation, user_message_id FROM memory_jobs WHERE id=%s AND status=%s AND lease_token=%s', (job_id, 'processing', token))
                job = cur.fetchone()
                if not job:
                    return
                user_id, epoch, generation, source_message_id = job
                cur.execute('SELECT epoch FROM memory_owners WHERE user_id=%s FOR UPDATE', (user_id,))
                owner = cur.fetchone()
                cur.execute('SELECT id FROM memory_jobs WHERE id=%s AND status=%s AND lease_token=%s FOR UPDATE', (job_id, 'processing', token))
                if not cur.fetchone():
                    return
                cur.execute('SELECT generation FROM memory_control WHERE id=1')
                control = cur.fetchone()
                if not owner or owner[0] != epoch or not control or control[0] != generation:
                    cur.execute("UPDATE memory_jobs SET status='cancelled', updated_at=%s WHERE id=%s", (now, job_id))
                    return
                store = PostgresStore(conn, index=_index())
                namespace = _namespace(user_id)
                changed = False
                for change in changes:
                    if not isinstance(change, dict):
                        continue
                    action, key = change.get('action'), str(change.get('id') or '')
                    text = str(change.get('text') or '').strip()
                    if action == 'add' and text:
                        store.put(namespace, uuid.uuid4().hex, {'text': text, 'source_message_id': source_message_id, 'created_at': now.isoformat()})
                        changed = True
                    elif action in ('update', 'delete') and key and store.get(namespace, key):
                        if action == 'delete':
                            store.delete(namespace, key)
                            changed = True
                        elif text:
                            old = store.get(namespace, key).value
                            store.put(namespace, key, {**old, 'text': text, 'source_message_id': source_message_id, 'updated_at': now.isoformat()})
                            changed = True
                cur.execute("UPDATE memory_jobs SET status='done', lease_token=NULL, lease_until=NULL, updated_at=%s WHERE id=%s", (now, job_id))
                if changed:
                    cur.execute('UPDATE memory_owners SET revision=revision+1 WHERE user_id=%s', (user_id,))


def work_once():
    from database.db import SessionLocal, MemoryJob
    claimed = _claim()
    if not claimed:
        return False
    job_id, token = claimed
    try:
        _process(job_id, token)
    except Exception as error:
        logger.warning('memory_job_failed job=%s kind=%s', job_id, type(error).__name__)
        with SessionLocal() as db:
            job = db.query(MemoryJob).filter_by(id=job_id, lease_token=token).with_for_update().first()
            if job:
                job.status = 'failed' if job.attempts >= settings.memory_job_max_attempts else 'pending'
                job.next_attempt_at = datetime.utcnow() + timedelta(seconds=min(300, 2 ** job.attempts * 5))
                job.error_code = type(error).__name__[:64]
                job.lease_token = None
                job.lease_until = None
                db.commit()
    return True


def maintain_once():
    """Periodically inspect small key-ordered batches; never hold a lock during an API call."""
    import psycopg
    from langgraph.store.postgres import PostgresStore
    from llm.llm_api_client import complete_sync
    now, token = datetime.utcnow(), uuid.uuid4().hex
    with psycopg.connect(_uri()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, epoch, revision, COALESCE(maintenance_cursor, '') "
                "FROM memory_owners WHERE maintenance_due <= %s "
                "AND (maintenance_lease_until IS NULL OR maintenance_lease_until < %s) "
                "ORDER BY maintenance_due LIMIT 1 FOR UPDATE SKIP LOCKED", (now, now))
            owner = cur.fetchone()
            if not owner:
                return False
            user_id, epoch, revision, cursor = owner
            cur.execute('UPDATE memory_owners SET maintenance_lease=%s, maintenance_lease_until=%s WHERE user_id=%s',
                        (token, now + timedelta(seconds=settings.memory_job_lease_seconds), user_id))
        conn.commit()
    try:
        with psycopg.connect(_uri()) as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT key, value FROM store WHERE prefix=%s AND key>%s ORDER BY key LIMIT 40',
                            ('.'.join(_namespace(user_id)), cursor))
                rows = cur.fetchall()
        changes = []
        if rows:
            records = [{'id': key, 'text': value.get('text', ''),
                        'created_at': value.get('created_at'), 'updated_at': value.get('updated_at')}
                       for key, value in rows]
            result = complete_sync([
                {'role': 'system', 'content': '维护用户长期记忆。只在已有内容足以判断时合并重复、修正明显冲突或删除已经明确失效的内容；不要凭空创造事实，也不要因为记忆较旧就删除。没有必要更改时不要调用工具。'},
                {'role': 'user', 'content': json.dumps({'当前日期': now.date().isoformat(), '记忆': records}, ensure_ascii=False)},
            ], task='planning', tools=_TOOL)
            for call in result.get('calls') or []:
                if call.get('name') == 'update_memories':
                    changes.extend(call.get('arguments', {}).get('changes') or [])
        with psycopg.connect(_uri(), autocommit=False) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute('SELECT epoch, revision, maintenance_lease FROM memory_owners WHERE user_id=%s FOR UPDATE', (user_id,))
                    current = cur.fetchone()
                    if not current or current[2] != token:
                        return True
                    if current[:2] != (epoch, revision):
                        cur.execute('UPDATE memory_owners SET maintenance_due=%s, maintenance_lease=NULL, maintenance_lease_until=NULL WHERE user_id=%s',
                                    (datetime.utcnow() + timedelta(minutes=1), user_id))
                        return True
                    store = PostgresStore(conn, index=_index())
                    allowed = {key for key, _ in rows}
                    changed = False
                    for change in changes:
                        if not isinstance(change, dict):
                            continue
                        action, key = change.get('action'), str(change.get('id') or '')
                        if key not in allowed or action not in ('update', 'delete'):
                            continue
                        item = store.get(_namespace(user_id), key)
                        if not item:
                            continue
                        if action == 'delete':
                            store.delete(_namespace(user_id), key)
                            changed = True
                        elif str(change.get('text') or '').strip():
                            value = {**item.value, 'text': change['text'].strip(), 'updated_at': now.isoformat()}
                            store.put(_namespace(user_id), key, value)
                            changed = True
                    next_cursor = rows[-1][0] if len(rows) == 40 else None
                    due = now if next_cursor else now + timedelta(days=30)
                    cur.execute('UPDATE memory_owners SET revision=revision+%s, maintenance_cursor=%s, maintenance_due=%s, maintenance_lease=NULL, maintenance_lease_until=NULL WHERE user_id=%s',
                                (int(changed), next_cursor, due, user_id))
    except Exception as error:
        logger.warning('memory_maintenance_failed user=%s kind=%s', user_id, type(error).__name__)
        with psycopg.connect(_uri()) as conn:
            with conn.cursor() as cur:
                cur.execute('UPDATE memory_owners SET maintenance_due=%s, maintenance_lease=NULL, maintenance_lease_until=NULL WHERE user_id=%s AND maintenance_lease=%s',
                            (datetime.utcnow() + timedelta(days=1), user_id, token))
            conn.commit()
    return True

