"""Campus chat entry: native tools and natural text, without business routing."""
import asyncio
from contextlib import suppress
from langgraph.graph import END, START, StateGraph

from agent.core.request_control import run_sync
from agent.runtime import registry
from agent.runtime.loop import run_turn
from agent.runtime.retrieval_reply import RETRIEVAL_REPLIES, terminal_retrieval_reply
from core.config import settings
from llm.llm_client import SYSTEM_PROMPT


def _chat_tool(name, arguments):
    try:
        return registry.call(name, arguments)
    except TimeoutError:
        return {'status': 'error', 'error': {'code': 'timeout', 'detail': '本次查询未能完成'}}


def _execute(state):
    from llm.llm_api_config import get_api_config
    api = get_api_config()
    on_output = state.get('on_output')
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "system", "content": "当前是校园问答会话，仅开放知识库检索。需要实际制作文件时，请引导用户打开侧边栏的演示文稿工作区；不要声称已启动制作。"},
                {"role": "system", "content": "本次API提供方配置为%s，模型标识为%s；该标识不能证明未公开的内部训练版本。" % (api.active_profile, api.active.model)},
                {"role": "system", "content": "引用本轮检索资料时，在对应陈述后使用 sources 中 citation_id 的标记，例如 [S1]。只使用本轮工具实际返回的标识，不自行编造编号；未引用的资料无需加标记。"},
                *state.get("history", []), {"role": "user", "content": state["question"]}]
    from conversations.source_references import tag_sources, reference
    identities = {}

    def execute(name, arguments):
        return tag_sources(_chat_tool(name, arguments), identities)

    result = run_turn(messages, task="planning", tools=registry.schemas(), execute=execute,
                      max_steps=settings.agent_max_steps, max_calls=settings.agent_max_tool_calls,
                      on_tool_result=terminal_retrieval_reply, on_output=on_output)
    if result.get('terminal'):
        return {'final_answer': result['text'], 'contexts': [], 'calls': result['calls'],
                'terminal': True}
    if result["paused"]:
        from llm.llm_api_client import complete_sync
        # Close an exhausted tool round with the same model and history.
        streaming = {'on_delta': lambda text: on_output('delta', text)} if on_output else {}
        response = complete_sync(messages, task="planning", **streaming)
        if response["calls"] or not response["text"].strip():
            raise ValueError("本轮执行额度已用完，模型未能返回说明")
        result["text"] = response["text"]
    contexts, seen = [], set()
    from documents.document_availability import require_ready, SourceUnavailable
    try:
        for output in result["results"]:
            if output.get("source_revision") is not None:
                require_ready(output["source_revision"])
            for source in output.get("sources") or []:
                identity = (source.get("doc_id"), source.get("chunk_id"), source.get("text_hash"))
                if identity in seen:
                    continue
                seen.add(identity)
                contexts.append(reference(source, output.get('source_revision')))
    except SourceUnavailable:
        return {'final_answer': RETRIEVAL_REPLIES['error'], 'contexts': [],
                'calls': result['calls'], 'terminal': True}
    return {"final_answer": result["text"], "contexts": contexts, "calls": result["calls"]}


def build_agent_graph():
    graph = StateGraph(dict)
    async def respond(state):
        return await run_sync(_execute, state)
    graph.add_node("respond", respond)
    graph.add_edge(START, "respond")
    graph.add_edge("respond", END)
    return graph.compile()


async def run_stream(history, question, *, conversation_id=None, owner_id=None, **_):
    events = asyncio.Queue()
    event_loop = asyncio.get_running_loop()

    def output(kind, text):
        event_loop.call_soon_threadsafe(events.put_nowait, (kind, text))

    async def produce():
        try:
            result = await build_agent_graph().ainvoke({"question": question,
                "history": list(history or []), 'on_output': output})
            await events.put(('result', result))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await events.put(('error', error))

    producer = asyncio.create_task(produce())
    text = ''
    try:
        while True:
            kind, payload = await events.get()
            if kind == 'error':
                raise payload
            if kind == 'result':
                result = payload
                break
            text = payload if kind == 'replace' else text + payload
            yield kind, payload
        if text != result['final_answer']:
            yield 'replace', result['final_answer']
    finally:
        if not producer.done():
            producer.cancel()
        with suppress(asyncio.CancelledError):
            await producer
    # The envelope is server metadata, not a model-authored writing contract.
    answer = {"status": "answer", "text": result["final_answer"], "contexts": result["contexts"],
              "gaps": [], "questions": [], "limitations": [], "suggestions": []}
    yield "done", {"contexts": result["contexts"], "calls": result["calls"],
                   "n_refs": len(result["contexts"]), "answer": answer,
                   "terminal": result.get('terminal', False),
                   "decision": "tool_call" if result["calls"] else "no_tool"}
