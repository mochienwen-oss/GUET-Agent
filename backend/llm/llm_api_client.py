"""Responses/Chat Completions transport with one configured model for all tasks."""
from copy import deepcopy
import json

import httpx

from llm.llm_api_config import get_api_config


def strict_schema(schema):
    result = deepcopy(schema)
    if 'properties' in result:
        required = result.get('required', [])
        result['properties'] = {k: strict_schema(v) if k in required else
            {'anyOf': [strict_schema(v), {'type': 'null'}]} for k, v in result['properties'].items()}
        result['required'] = list(result['properties'])
    if 'items' in result:
        result['items'] = strict_schema(result['items'])
    return result


def restore_optional_fields(value, schema):
    if isinstance(value, dict):
        properties = schema.get('properties', {})
        return {k: restore_optional_fields(v, properties.get(k, {})) for k, v in value.items()
                if not (k in properties and k not in schema.get('required', []) and v is None)}
    if isinstance(value, list):
        return [restore_optional_fields(v, schema.get('items', {})) for v in value]
    return value


def _response_input(messages):
    out = []
    for message in messages:
        if '_response_items' in message:
            out.extend(deepcopy(message['_response_items']))
            continue
        role = message['role']
        if role == 'tool':
            out.append({'type': 'function_call_output', 'call_id': message['tool_call_id'],
                        'output': message.get('content') or ''})
        elif role == 'assistant' and message.get('tool_calls'):
            if message.get('content'):
                out.append({'role': role, 'content': message['content']})
            for call in message['tool_calls']:
                function = call['function']
                arguments = function['arguments']
                out.append({'type': 'function_call', 'call_id': call['id'], 'name': function['name'],
                            'arguments': arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)})
        else:
            out.append(deepcopy(message))
    return out


def build_request(messages, *, task, tools=None, tool_choice='auto', schema=None, json_object=False, stream=False):
    config = get_api_config()
    profile = config.active
    limits = config.tasks[task]
    body = {**deepcopy(profile.parameters), **deepcopy(limits.parameters), 'model': profile.model, 'stream': stream}
    if profile.protocol == 'responses':
        endpoint = '/responses'
        body.update(input=_response_input(messages), max_output_tokens=limits.max_output_tokens)
        if tools:
            body['tools'] = [{'type': 'function', 'strict': False, **deepcopy(tool['function'])} for tool in tools]
            body['tool_choice'] = ({'type': 'function', 'name': tool_choice['function']['name']}
                                   if isinstance(tool_choice, dict) else tool_choice)
        if schema is not None:
            body['text'] = {'format': ({'type': 'json_schema', 'name': 'structured_result', 'strict': True,
                                       'schema': strict_schema(schema)} if profile.structured_output == 'json_schema'
                                      else {'type': 'json_object'})}
        elif json_object:
            body['text'] = {'format': {'type': 'json_object'}}
    else:
        endpoint = '/chat/completions'
        # Combine only leading trusted instructions; preserve history/tool boundaries.
        end = 0
        while end < len(messages) and messages[end]['role'] == 'system':
            end += 1
        packed = ([dict(messages[0], content='\n\n'.join(m['content'] for m in messages[:end]))] + deepcopy(messages[end:])
                  if end > 1 else deepcopy(messages))
        body.update(messages=packed, **{profile.chat_max_tokens_field: limits.max_output_tokens})
        if tools:
            body.update(tools=deepcopy(tools), tool_choice=tool_choice)
        if schema is not None:
            body['response_format'] = ({'type': 'json_schema', 'json_schema': {'name': 'structured_result',
                'strict': True, 'schema': strict_schema(schema)}} if profile.structured_output == 'json_schema'
                else {'type': 'json_object'})
        elif json_object:
            body['response_format'] = {'type': 'json_object'}
    return config, profile.base_url + endpoint, body


def _calls(items):
    calls = []
    for item in items:
        function = item.get('function', item)
        arguments = function.get('arguments') or '{}'
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                raise ValueError('API工具参数不是完整JSON对象') from None
        if not isinstance(arguments, dict) or not function.get('name'):
            raise ValueError('API工具调用格式无效')
        call_id = item.get('call_id') or item.get('id')
        if not call_id:
            raise ValueError('API工具调用缺少调用标识')
        calls.append({'id': call_id, 'name': function['name'], 'arguments': arguments})
    return calls


def _normalize_usage(usage):
    """把不同平台的用量字段统一成同一组键，换供应商不影响统计。

    已覆盖：Responses（``input_tokens`` / ``output_tokens``）、OpenAI 兼容
    chat/completions（``prompt_tokens`` / ``completion_tokens``）、DeepSeek 原生
    chat 的 ``prompt_cache_hit_tokens``。未知平台即使认不出来，也会留下
    ``provider_keys`` 便于排查，而不是静默记成 0。
    """
    if not isinstance(usage, dict) or not usage:
        return None

    def pick(*names):
        for name in names:
            value = usage.get(name)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                return int(value)
        return 0

    def number(container, name):
        if isinstance(container, dict):
            value = container.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return int(value)
        return 0

    prompt_details = usage.get('input_tokens_details') or usage.get('prompt_tokens_details')
    output_details = usage.get('output_tokens_details') or usage.get('completion_tokens_details')
    input_tokens = pick('input_tokens', 'prompt_tokens')
    output_tokens = pick('output_tokens', 'completion_tokens')
    total_tokens = pick('total_tokens') or input_tokens + output_tokens
    normalized = {'input_tokens': input_tokens, 'output_tokens': output_tokens,
                  'total_tokens': total_tokens,
                  'cached_tokens': pick('cached_tokens', 'prompt_cache_hit_tokens')
                                   or number(prompt_details, 'cached_tokens'),
                  'reasoning_tokens': pick('reasoning_tokens')
                                      or number(output_details, 'reasoning_tokens')}
    if not (input_tokens or output_tokens or total_tokens):
        normalized['provider_keys'] = sorted(str(key) for key in usage)[:12]
    return normalized


def _result(data, protocol):
    if protocol == 'responses':
        if data.get('status') != 'completed':
            raise ValueError('API响应未完整结束，未交付不完整内容')
        items = data.get('output') or []
        if any(part.get('type') == 'refusal' for item in items for part in item.get('content', [])):
            raise ValueError('API拒绝生成本轮内容')
        text = data.get('output_text') or ''.join(part.get('text', '') for item in items
            if item.get('type') == 'message' for part in item.get('content', []) if part.get('type') == 'output_text')
        calls = _calls([item for item in items if item.get('type') == 'function_call'])
    else:
        choice = data['choices'][0]
        if choice.get('finish_reason') in {'length', 'content_filter'}:
            raise ValueError('API响应被截断或过滤，未交付不完整内容')
        message = choice['message']
        if message.get('refusal'):
            raise ValueError('API拒绝生成本轮内容')
        text, calls = message.get('content') or '', _calls(message.get('tool_calls') or [])
    message = {'role': 'assistant', 'content': text}
    if protocol == 'responses' and items:
        message['_response_items'] = deepcopy(items)
    elif protocol != 'responses' and data['choices'][0]['message'].get('reasoning_content') is not None:
        message['reasoning_content'] = data['choices'][0]['message']['reasoning_content']
    if calls:
        message['tool_calls'] = [{'id': call['id'], 'type': 'function', 'function': {
            'name': call['name'], 'arguments': json.dumps(call['arguments'], ensure_ascii=False)}} for call in calls]
    return {'text': text, 'calls': calls, 'message': message,
            'model': data.get('model'), 'id': data.get('id'),
            'usage': _normalize_usage(data.get('usage'))}


def _timeout(config):
    from agent.core.request_control import remaining_seconds
    profile = config.active
    return httpx.Timeout(remaining_seconds(profile.timeout_seconds), connect=profile.connect_timeout_seconds)


def _http_ok(response, config, task):
    if response.status_code != 200:
        # Provider error messages can echo credentials or prompt contents.
        raise RuntimeError('API调用失败：profile=%s task=%s HTTP=%s' % (config.active_profile, task, response.status_code))


def _request_audit(trace, config, url, body, task):
    if trace is None:
        return
    row = {'provider': config.active_profile, 'protocol': config.active.protocol, 'model': body['model'],
           'url': url, 'task': task, 'config_sha256': config.fingerprint, 'request_fields': list(body),
           'generation': {k: v for k, v in body.items() if k not in {'messages', 'input', 'tools', 'text', 'response_format'}}}
    trace.setdefault('api_requests', []).append(row)


def _response_audit(trace, config, result, task):
    if trace is None:
        return
    row = {'provider': config.active_profile, 'task': task, 'model': result['model'], 'id': result['id'], 'usage': result['usage']}
    trace.setdefault('api_responses', []).append(row)


async def complete(messages, *, task, tools=None, tool_choice='auto', schema=None, json_object=False, trace=None):
    config, url, body = build_request(messages, task=task, tools=tools, tool_choice=tool_choice,
                                      schema=schema, json_object=json_object)
    key = config.active.api_key()
    _request_audit(trace, config, url, body, task)
    async with httpx.AsyncClient(timeout=_timeout(config)) as client:
        response = await client.post(url, json=body, headers={'Authorization': 'Bearer ' + key})
        _http_ok(response, config, task)
        result = _result(response.json(), config.active.protocol)
    _response_audit(trace, config, result, task)
    return result


class _StreamResult:
    """Collect one native streamed response without discarding tool history."""
    def __init__(self, protocol):
        self.protocol = protocol
        self.finished = False
        self.result = None
        self.calls = {}
        self.text = []
        self.reasoning = []
        self.refusal = []
        self.model = self.id = self.usage = None
        self.finish_reason = None

    def feed(self, raw):
        if raw == '[DONE]' and self.protocol == 'chat_completions':
            return ''
        event = json.loads(raw)
        if self.protocol == 'responses':
            kind = event.get('type')
            if kind == 'response.output_text.delta':
                return event.get('delta') or ''
            if kind == 'response.completed':
                self.result = _result(event['response'], 'responses')
                self.finished = True
            elif kind in {'error', 'response.failed', 'response.incomplete'}:
                raise ValueError('API流未完整结束：' + kind)
            return ''
        if event.get('error'):
            raise RuntimeError('API流返回错误')
        self.model = event.get('model') or self.model
        self.id = event.get('id') or self.id
        self.usage = event.get('usage') or self.usage
        text = ''
        for choice in event.get('choices') or []:
            if choice.get('index', 0) != 0:
                continue
            reason = choice.get('finish_reason')
            if reason in {'length', 'content_filter'}:
                raise ValueError('API流被截断或过滤')
            if reason in {'stop', 'tool_calls'}:
                self.finished, self.finish_reason = True, reason
            delta = choice.get('delta') or {}
            content = delta.get('content') or ''
            self.text.append(content)
            text += content
            if delta.get('reasoning_content') is not None:
                self.reasoning.append(delta['reasoning_content'])
            if delta.get('refusal'):
                self.refusal.append(delta['refusal'])
            for item in delta.get('tool_calls') or []:
                slot = self.calls.setdefault(item['index'], {'id': '', 'name': '', 'arguments': ''})
                if item.get('id'):
                    slot['id'] = item['id']
                function = item.get('function') or {}
                slot['name'] += function.get('name') or ''
                slot['arguments'] += function.get('arguments') or ''
        return text

    def finish(self):
        if not self.finished:
            raise ValueError('API流在终止事件前断开')
        if self.protocol == 'responses':
            return self.result
        message = {'role': 'assistant', 'content': ''.join(self.text)}
        if self.reasoning:
            message['reasoning_content'] = ''.join(self.reasoning)
        if self.refusal:
            message['refusal'] = ''.join(self.refusal)
        if self.calls:
            message['tool_calls'] = [{'id': row['id'], 'type': 'function', 'function': {
                'name': row['name'], 'arguments': row['arguments']}}
                for _, row in sorted(self.calls.items())]
        return _result({'id': self.id, 'model': self.model, 'usage': self.usage,
                        'choices': [{'message': message, 'finish_reason': self.finish_reason}]}, 'chat_completions')


def complete_sync(messages, *, task, json_object=False, tools=None, tool_choice='auto', on_delta=None):
    config, url, body = build_request(messages, task=task, json_object=json_object,
                                      tools=tools, tool_choice=tool_choice, stream=on_delta is not None)
    key = config.active.api_key()
    with httpx.Client(timeout=_timeout(config)) as client:
        if on_delta is not None:
            from agent.core.request_control import check_cancelled
            collector = _StreamResult(config.active.protocol)
            emitted = False
            with client.stream('POST', url, json=body, headers={'Authorization': 'Bearer ' + key}) as response:
                _http_ok(response, config, task)
                for line in response.iter_lines():
                    check_cancelled()
                    if not line.startswith('data:') or not line[5:].strip():
                        continue
                    text = collector.feed(line[5:].strip())
                    if text:
                        emitted = True
                        on_delta(text)
                    if collector.finished and config.active.protocol == 'responses':
                        break
            result = collector.finish()
            if not emitted and not result['calls'] and result['text']:
                on_delta(result['text'])
            return result
        response = client.post(url, json=body, headers={'Authorization': 'Bearer ' + key})
        _http_ok(response, config, task)
        return _result(response.json(), config.active.protocol)


async def stream(messages, *, tools=None, tool_choice='auto'):
    config, url, body = build_request(messages, task='planning', tools=tools, tool_choice=tool_choice, stream=True)
    key = config.active.api_key()
    collector, emitted = _StreamResult(config.active.protocol), False
    async with httpx.AsyncClient(timeout=_timeout(config)) as client:
        async with client.stream('POST', url, json=body, headers={'Authorization': 'Bearer ' + key}) as response:
            _http_ok(response, config, 'planning')
            async for line in response.aiter_lines():
                if not line.startswith('data:'):
                    continue
                raw = line[5:].strip()
                if not raw:
                    continue
                text = collector.feed(raw)
                if text and not tools:
                    emitted = True
                    yield 'delta', text
                if collector.finished and config.active.protocol == 'responses':
                    break
    result = collector.finish()
    if result['calls']:
        yield 'calls', result['calls']
    elif result['text'] and (tools or not emitted):
        yield 'delta', result['text']
