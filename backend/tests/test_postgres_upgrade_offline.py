"""Isolated migration and memory checks; never use existing application data."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from dotenv import dotenv_values
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'run/python-pg-deps'))
sys.path.insert(0, str(ROOT / 'backend'))
PYTHON = sys.executable
MYSQL = r'C:\Program Files\MySQL\MySQL Server 8.4\bin\mysqld.exe'
MYSQLADMIN = r'C:\Program Files\MySQL\MySQL Server 8.4\bin\mysqladmin.exe'


@pytest.fixture(scope='session')
def isolated_db(tmp_path_factory):
    import psycopg
    from psycopg import sql
    values = dotenv_values(ROOT / 'run/postgres.local.env')
    configured = make_url(values['DATABASE_URL'])
    assert configured.host in ('127.0.0.1', 'localhost', '::1') and configured.port == 55432
    database = 'guet_test_' + uuid.uuid4().hex[:12]
    admin_url = configured.set(drivername='postgresql', database='postgres').render_as_string(hide_password=False)
    test_url = configured.set(database=database).render_as_string(hide_password=False)
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {} ENCODING {}').format(sql.Identifier(database), sql.Literal('UTF8')))
    previous = {name: os.environ.get(name) for name in ('DATABASE_URL', 'MYSQL_URL', 'REDIS_URL')}
    os.environ['DATABASE_URL'] = test_url
    os.environ['MYSQL_URL'] = 'mysql+pymysql://root@127.0.0.1:33307/guet_isolated?charset=utf8mb4'
    os.environ['REDIS_URL'] = values['REDIS_URL']
    try:
        yield {'url': test_url, 'mysql': os.environ['MYSQL_URL'], 'tmp': tmp_path_factory.mktemp('postgres-upgrade')}
    finally:
        from database.db import engine
        engine.dispose()
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        assert database.startswith('guet_test_') and len(database) == 22
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)))


@pytest.fixture(scope='session')
def isolated_mysql(isolated_db):
    # A separate MySQL daemon and data directory; never connect to the application instance.
    data = isolated_db['tmp'] / 'mysql-data'
    data.mkdir()
    data_path = str(data)
    if os.name == 'nt':
        import ctypes
        short = ctypes.create_unicode_buffer(32768)
        if not ctypes.windll.kernel32.GetShortPathNameW(data_path, short, len(short)):
            raise RuntimeError('无法取得测试数据目录的 Windows 短路径')
        data_path = short.value
    hidden = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    init = subprocess.run([MYSQL, '--initialize-insecure', '--console', f'--datadir={data_path}'],
                          capture_output=True, text=True, encoding='utf-8', errors='replace', creationflags=hidden)
    assert init.returncode == 0, init.stderr[-1200:]
    error_log = (isolated_db['tmp'] / 'mysql-error.log').open('w+', encoding='utf-8')
    server = subprocess.Popen([MYSQL, '--console', f'--datadir={data_path}', '--port=33307',
                               '--bind-address=127.0.0.1', '--mysqlx=0'],
                              stdout=subprocess.DEVNULL, stderr=error_log, creationflags=hidden)
    admin = create_engine('mysql+pymysql://root@127.0.0.1:33307/mysql?charset=utf8mb4')
    try:
        for _ in range(60):
            try:
                with admin.begin() as conn:
                    conn.exec_driver_sql('CREATE DATABASE guet_isolated CHARACTER SET utf8mb4')
                break
            except Exception:
                if server.poll() is not None:
                    error_log.flush()
                    raise RuntimeError('临时 MySQL 提前退出：' + (isolated_db['tmp'] / 'mysql-error.log').read_text(encoding='utf-8', errors='replace')[-1200:])
                time.sleep(0.5)
        else:
            raise RuntimeError('临时 MySQL 未就绪')
        yield
    finally:
        admin.dispose()
        subprocess.run([MYSQLADMIN, '-h', '127.0.0.1', '-P', '33307', '-u', 'root', 'shutdown'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, creationflags=hidden)
        if server.poll() is None:
            server.terminate()
            server.wait(timeout=30)
        error_log.close()


def invoke_script(path, isolated_db, **extra):
    env = dict(os.environ, DATABASE_URL=isolated_db['url'], MYSQL_URL=isolated_db['mysql'], **extra)
    run = subprocess.run([PYTHON, str(ROOT / path), '--source-stopped'], cwd=ROOT, env=env,
                         text=True, encoding='utf-8', errors='replace', capture_output=True, timeout=90)
    assert run.returncode == 0, (run.stdout + run.stderr)[-3500:]
    return run.stdout


def test_business_and_vector_migration(isolated_db, isolated_mysql):
    from database.db import Base, User, Session, Conversation, Message, Document, SystemSetting
    from qdrant_client import QdrantClient, models
    from agent.tools.search_regulations.scripts.index_ops.postgres_index import PostgresIndex

    source = create_engine(isolated_db['mysql'])
    Base.metadata.create_all(source)
    with source.begin() as conn:
        conn.execute(User.__table__.insert(), {'id': 1, 'username': 'user1', 'password_hash': 'mock', 'name': '张三🌱', 'role': 'user'})
        conn.execute(Session.__table__.insert(), {'token': 'offline-token', 'user_id': 1})
        conn.execute(Conversation.__table__.insert(), {'id': 4, 'user_id': 1, 'title': '学业规划', 'context_json': {'年级': '2025级'}, 'context_revision': 1})
        conn.execute(Message.__table__.insert(), {'id': 7, 'conversation_id': 4, 'role': 'assistant', 'content': '中文引用🙂',
            'retrieved_docs': [{'doc_id': '1', 'citation_id': 'S1', 'text': '规定正文'}], 'answer_json': {'说明': '中英文'}})
        conn.execute(Document.__table__.insert(), {'id': 1, 'title': '本科生规定', 'source_type': 'md', 'chunk_count': 2,
            'status': 'ready', 'category': '规章制度', 'content': '规定正文🙂\n2025年7月26日印发', 'source_metadata': {'年份': 2025}})
        conn.execute(SystemSetting.__table__.insert(), {'key': 'test', 'value': '本地测试'})
    source.dispose()
    invoke_script('scripts/knowledge/migrate_mysql_to_postgres.py', isolated_db)
    target = create_engine(isolated_db['url'])
    with target.connect() as conn:
        assert conn.execute(select(Document.content)).scalar_one() == '规定正文🙂\n2025年7月26日印发'
        assert conn.execute(select(Message.retrieved_docs)).scalar_one()[0]['citation_id'] == 'S1'
        assert conn.execute(select(User.name)).scalar_one() == '张三🌱'
        new_id = conn.execute(User.__table__.insert().values(username='user2', password_hash='mock', name='第二人', role='user').returning(User.id)).scalar_one()
        assert new_id > 1
        conn.rollback()

    qpath = isolated_db['tmp'] / 'qdrant'
    client = QdrantClient(path=str(qpath))
    client.create_collection(collection_name='offline', vectors_config={'dense': models.VectorParams(size=3, distance=models.Distance.COSINE)},
                             sparse_vectors_config={'sparse': models.SparseVectorParams()})
    client.upsert(collection_name='offline', points=[
        models.PointStruct(id=1000001, vector={'dense': [1.0, 0.0, 0.0], 'sparse': models.SparseVector(indices=[1, 2], values=[2.0, 0.5])},
                           payload={'document_id': 1, 'chunk_idx': 1, 'text': '中文第一条🙂', 'source_span': [0, 6], 'block_type': 'paragraph'}),
        models.PointStruct(id=1000002, vector={'dense': [0.0, 1.0, 0.0], 'sparse': models.SparseVector(indices=[2], values=[3.0])},
                           payload={'document_id': 1, 'chunk_idx': 2, 'text': '第二条', 'source_span': [7, 10], 'block_type': 'paragraph'})])
    client.close()
    invoke_script('scripts/knowledge/migrate_qdrant_to_pgvector.py', isolated_db, QDRANT_PATH=str(qpath), QDRANT_COLLECTION='offline')
    index = PostgresIndex(target)
    constraint = models.Filter(must=[models.FieldCondition(key='document_id', match=models.MatchValue(value=1))])
    dense = index.query_points('offline', [1.0, 0.0, 0.0], using='dense', limit=2, query_filter=constraint).points
    sparse = index.query_points('offline', models.SparseVector(indices=[2], values=[1.0]), using='sparse', limit=2, query_filter=constraint).points
    assert [row.id for row in dense] == [1000001, 1000002]
    assert [row.id for row in sparse] == [1000002, 1000001]
    assert dense[0].payload['text'] == '中文第一条🙂'
    assert index.count('offline', count_filter=constraint).count == 2
    first, offset = index.scroll('offline', limit=1)
    second, more = index.scroll('offline', limit=1, offset=offset)
    assert first[0].id == 1000001 and second[0].id == 1000002 and more is None
    target.dispose()


def test_long_term_memory_isolation_and_runtime_context(isolated_db, monkeypatch):
    from core.config import settings
    from agent.runtime import user_memory, graph, registry
    monkeypatch.setattr(settings, 'database_url', isolated_db['url'])
    user_memory.setup()
    user_memory.write(10, 'profile', '2025级人工智能专业')
    user_memory.write(11, 'profile', '另一位用户')
    assert user_memory.read(10)['profile'] == '2025级人工智能专业'
    assert user_memory.read(11)['profile'] == '另一位用户'
    user_memory.write(10, 'profile', '')
    assert 'profile' not in user_memory.read(10) and user_memory.read(11)['profile'] == '另一位用户'
    seen = []
    def answer(state, context):
        seen.append((context.user_id, context.conversation_id, list(state['history'])))
        return {'final_answer': '已收到', 'contexts': [], 'calls': []}
    monkeypatch.setattr(graph, '_execute', answer)
    graph.build_memory_graph.cache_clear()
    async def run(history, question, owner, conversation):
        return [event async for event in graph.run_stream(history, question, owner_id=owner, conversation_id=conversation)]
    first = asyncio.run(run([], '第一轮', 10, 4))
    history = [{'role': 'user', 'content': '第一轮'}, {'role': 'assistant', 'content': '已收到'}]
    asyncio.run(run(history, '第二轮', 10, 4))
    asyncio.run(run([], '其他人', 11, 4))
    graph.build_memory_graph.cache_clear()  # simulate an application process restart
    asyncio.run(run(history, '重启后', 10, 4))
    assert first[-1][1]['answer']['text'] == '已收到'
    assert seen == [(10, 4, []), (10, 4, history), (11, 4, []), (10, 4, history)]
    spec = registry.ToolSpec('test', 'test', registry.SearchRegulationsArgs,
                             lambda runtime_context, **kw: {'owner': runtime_context.user_id}, needs_context=True)
    result = registry.execute(spec, {'query': '问题'}, runtime_context=graph.RequestContext(10, 4, lambda *_: None))
    assert result['owner'] == 10


def test_redis_catalog_version_and_failure_fallback(isolated_db, monkeypatch):
    from core.config import settings
    from documents.catalog import catalog_cache
    import redis
    monkeypatch.setattr(settings, 'redis_url', os.environ['REDIS_URL'])
    monkeypatch.setattr(catalog_cache, '_client', None)
    path = isolated_db['tmp'] / 'catalog.json'
    path.write_text(json.dumps({'documents': [{'title': '中文资料'}]}, ensure_ascii=False), encoding='utf-8')
    assert catalog_cache.read_catalog(path)['documents'][0]['title'] == '中文资料'
    before = path.stat()
    key1 = f'guet:catalog:{path}:{before.st_mtime_ns}:{before.st_size}'
    path.write_text(json.dumps({'documents': [{'title': '更新后的资料'}]}, ensure_ascii=False), encoding='utf-8')
    assert catalog_cache.read_catalog(path)['documents'][0]['title'] == '更新后的资料'
    after = path.stat()
    key2 = f'guet:catalog:{path}:{after.st_mtime_ns}:{after.st_size}'
    client = redis.Redis.from_url(os.environ['REDIS_URL'], decode_responses=True)
    assert 0 < client.ttl(key1) <= 360 and 0 < client.ttl(key2) <= 360
    client.delete(key1, key2)  # only this test's two keys
    monkeypatch.setattr(catalog_cache, '_redis', lambda: (_ for _ in ()).throw(ConnectionError('offline')))
    assert catalog_cache.read_catalog(path)['documents'][0]['title'] == '更新后的资料'


def test_authenticated_memory_and_conversation_ownership(isolated_db, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from fastapi.responses import JSONResponse
    from sqlalchemy.orm import Session as DbSession
    from core.config import settings
    from database.db import Base, User, Session, Conversation, Message, engine, get_db
    from agent.runtime import user_memory
    from routers.chat import router
    from core.errors import ApiError

    monkeypatch.setattr(settings, 'database_url', isolated_db['url'])
    Base.metadata.create_all(engine)
    user_memory.setup()
    with DbSession(engine) as db:
        db.add_all([User(id=1, username='alice', password_hash='mock', name='甲', role='user'),
                    User(id=2, username='bob', password_hash='mock', name='乙', role='user')])
        db.flush()
        db.add_all([Session(token='alice-offline', user_id=1), Session(token='bob-offline', user_id=2),
                    Conversation(id=1, user_id=1, title='甲的会话')])
        db.flush()
        db.add(Message(conversation_id=1, role='assistant', content='甲的会话内容'))
        db.commit()
    user_memory.write(1, 'profile', '甲的专业')
    user_memory.write(2, 'profile', '乙的专业')
    app = FastAPI()
    @app.exception_handler(ApiError)
    async def api_error(_request, exc):
        return JSONResponse(status_code=exc.status_code, content={'error': exc.message})
    app.include_router(router, prefix='/api')
    def session_override():
        with DbSession(engine) as db:
            yield db
    app.dependency_overrides[get_db] = session_override
    with TestClient(app, raise_server_exceptions=False) as client:
        alice = {'Authorization': 'Bearer alice-offline'}
        bob = {'Authorization': 'Bearer bob-offline'}
        assert client.get('/api/memory', headers=alice).json()['profile'] == '甲的专业'
        assert client.get('/api/memory', headers=bob).json()['profile'] == '乙的专业'
        assert client.get('/api/memory').status_code == 401
        assert client.get('/api/conversations/1/messages', headers=alice).status_code == 200
        assert client.get('/api/conversations/1/messages', headers=bob).status_code == 404
