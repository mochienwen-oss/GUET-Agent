"""Token-budgeted conversation history and its persistent summary."""
import json
from functools import lru_cache
from pathlib import Path

from core.config import settings
from agent.runtime.user_memory import _uri


@lru_cache(maxsize=1)
def _tokenizer():
    from tokenizers import Tokenizer
    from core.paths import PROJECT_ROOT
    return Tokenizer.from_file(str(Path(PROJECT_ROOT) / 'model' / 'embedding_m3' / 'tokenizer.json'))


def _tokens(text):
    return int(len(_tokenizer().encode(text, add_special_tokens=False).ids)
               * settings.memory_token_estimate_multiplier)


def check_request(messages, tools):
    from llm.llm_api_config import get_api_config
    api = get_api_config()
    size = _tokens(json.dumps({'messages': messages, 'tools': tools}, ensure_ascii=False))
    budget = int(settings.memory_context_window_tokens * settings.memory_context_safety_fraction)
    if size + api.tasks['planning'].max_output_tokens > budget:
        from core.errors import ApiError
        raise ApiError(400, '当前上下文或工具结果过长，无法安全发送；请缩小本次请求。')


def _namespace(user_id, conversation_id):
    return ('users', str(int(user_id)), 'conversations', str(int(conversation_id)))


def history(messages, question, user_id, conversation_id):
    from langgraph.store.postgres import PostgresStore
    from llm.llm_api_client import complete_sync

    namespace = _namespace(user_id, conversation_id)
    with PostgresStore.from_conn_string(_uri()) as store:
        item = store.get(namespace, 'summary')
        saved = item.value if item else {}
        summary = saved.get('text', '')
        covered_id = saved.get('covered_id', 0)
        remaining = [row for row in messages if row['id'] > covered_id]
        budget = min(settings.conversation_history_token_threshold,
                     int(settings.memory_context_window_tokens * settings.memory_context_safety_fraction))
        total = _tokens(summary) + _tokens(question) + sum(_tokens(row['content']) for row in remaining)
        if total > budget and remaining:
            # Keep the most recent half of the budget as full messages.
            recent_tokens = _tokens(question)
            split = len(remaining)
            while split > 0:
                size = _tokens(remaining[split - 1]['content'])
                if recent_tokens + size > budget // 2:
                    break
                recent_tokens += size
                split -= 1
            if split:
                older = remaining[:split]
                while older:
                    chunk, size = [], _tokens(summary)
                    for row in older:
                        row_size = _tokens(row['content'])
                        if size + row_size > budget // 3:
                            break
                        chunk.append(row)
                        size += row_size
                    if not chunk:
                        raise ValueError('单条历史消息过长，无法安全压缩上下文')
                    response = complete_sync([
                        {'role': 'system', 'content': '将历史对话压缩为简洁摘要，保留用户需求、重要上下文、已有结论与尚未解决的问题。仅依据提供的内容，不把摘要当作事实依据。'},
                        {'role': 'user', 'content': json.dumps({'已有摘要': summary, '需要压缩的对话': [
                            {'role': row['role'], 'content': row['content']} for row in chunk]}, ensure_ascii=False)},
                    ], task='planning')
                    updated = response['text'].strip()
                    if not updated:
                        raise ValueError('会话摘要生成失败')
                    summary = updated
                    store.put(namespace, 'summary', {'text': summary, 'covered_id': chunk[-1]['id']})
                    older = older[len(chunk):]
                remaining = remaining[split:]
    result = []
    if summary:
        result.append({'role': 'user', 'content': '以下是本会话较早对话的摘要，仅供理解上下文，可能不完整：\n' + summary})
    result.extend({'role': row['role'], 'content': row['content']} for row in remaining)
    return result


def delete(user_id, conversation_id):
    from langgraph.store.postgres import PostgresStore
    with PostgresStore.from_conn_string(_uri()) as store:
        store.delete(_namespace(user_id, conversation_id), 'summary')
