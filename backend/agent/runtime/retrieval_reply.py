"""Server-authored final replies for unsuccessful campus retrieval."""

RETRIEVAL_REPLIES = {
    'no_evidence': '目前未找到可用依据，暂时无法据此回答。',
    'irrelevant_result': '检索到的资料无法支持这个问题。',
    'error': '本次查询未能完成，暂时无法确认。',
}


def terminal_retrieval_reply(name, result):
    if name == 'search_regulations':
        return RETRIEVAL_REPLIES.get(result.get('status'))
    return None
