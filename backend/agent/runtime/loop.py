"""One provider-native tool loop for chat and file-making tasks."""
import json
import logging
import time

from agent.core.request_control import check_cancelled
from llm.llm_api_client import complete_sync

logger = logging.getLogger('uvicorn.error')


def run_turn(messages, *, task, tools, execute, max_steps, max_calls,
             on_progress=None, requests_left=None, on_tool_result=None, on_output=None, on_usage=None,
             prepare=None):
    calls, results, usage = [], [], []
    for step in range(max_steps):
        check_cancelled()
        if requests_left is not None and step >= requests_left:
            break
        # 每次请求前都有机会整理历史（例如折叠旧工具结果）；不传则完全保持原样。
        if prepare is not None:
            prepare(step, messages)
        if on_progress:
            on_progress("request", None)
        request_started = time.monotonic()
        logger.info('agent_api_start task=%s step=%d', task, step + 1)
        try:
            streaming = {'on_delta': lambda text: on_output('delta', text)} if on_output else {}
            response = complete_sync(messages, task=task, tools=tools,
                                     tool_choice="auto" if max_calls is None or len(calls) < max_calls else "none", **streaming)
        except BaseException as error:
            logger.warning('agent_api_failed task=%s step=%d elapsed=%.2fs kind=%s',
                           task, step + 1, time.monotonic() - request_started, type(error).__name__)
            raise
        logger.info('agent_api_done task=%s step=%d elapsed=%.2fs calls=%d',
                    task, step + 1, time.monotonic() - request_started, len(response.get('calls') or []))
        usage.append(response.get("usage"))
        messages.append(response["message"])
        if on_usage:
            on_usage(response.get('usage'))
        proposed = response.get("calls") or []
        text = response.get("text") or ""
        if proposed and on_output:
            # A tool-selection preface is not the final answer.
            on_output('replace', '')
        if not proposed:
            if not text.strip():
                raise ValueError("模型未返回文本或工具调用")
            if on_progress:
                on_progress("message", text)
            return {"text": text, "paused": False, "calls": calls,
                    "results": results, "usage": usage}
        for index, call in enumerate(proposed):
            started = time.monotonic()
            logger.info('agent_tool_start task=%s tool=%s', task, call['name'])
            try:
                check_cancelled()
                if max_calls is not None and len(calls) >= max_calls:
                    result = {"status": "error", "error": {
                        "code": "tool_budget", "detail": "本次执行额度已用完，该调用没有执行"}}
                else:
                    if on_progress:
                        on_progress('tool_start', {'name': call['name']})
                    result = execute(call["name"], call["arguments"])
                if not isinstance(result, dict):
                    raise TypeError("工具必须返回结果对象")
            except BaseException as error:
                logger.warning('agent_tool_failed task=%s tool=%s elapsed=%.2fs kind=%s',
                               task, call['name'], time.monotonic() - started, type(error).__name__)
                # Never resume an API history with unmatched calls, and never
                # silently replay a write whose completion is uncertain.
                for pending in proposed[index:]:
                    messages.append({"role": "tool", "tool_call_id": pending["id"],
                                     "content": json.dumps({"status": "error", "error": {
                                         "code": "interrupted", "detail": "执行中断；部分文件可能已写入，继续时先读取实际文件，不自动重放操作"}}, ensure_ascii=False)})
                if on_progress:
                    on_progress("interrupted", None)
                raise
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": json.dumps(result, ensure_ascii=False)})
            log = {"name": call["name"], "args": call["arguments"],
                   "status": result.get("status", "success"),
                   "ms": int((time.monotonic() - started) * 1000),
                   "hits": len(result.get("sources") or [])}
            calls.append(log)
            results.append(result)
            logger.info('agent_tool_done task=%s tool=%s elapsed=%.2fs status=%s',
                        task, call['name'], time.monotonic() - started, log['status'])
            if on_progress:
                on_progress("tool", log)
            terminal_text = on_tool_result(call['name'], result) if on_tool_result else None
            if terminal_text is not None:
                # Close the provider's remaining call IDs without executing them.
                for pending in proposed[index + 1:]:
                    messages.append({"role": "tool", "tool_call_id": pending["id"],
                                     "content": json.dumps({"status": "error", "error": {
                                         "code": "turn_ended", "detail": "本轮已由后台结束，该调用未执行"}}, ensure_ascii=False)})
                return {"text": terminal_text, "paused": False, "terminal": True,
                        "calls": calls, "results": results, "usage": usage}
    return {"text": "", "paused": True, "calls": calls, "results": results, "usage": usage}
