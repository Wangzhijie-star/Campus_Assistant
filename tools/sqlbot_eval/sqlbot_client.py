from __future__ import annotations

import json
from .models import Event


class ProtocolError(RuntimeError):
    pass


def unwrap_response(value):
    """ResponseMiddleware wraps ordinary JSON; SSE remains unwrapped."""
    if isinstance(value, dict) and all(key in value for key in ('code', 'data', 'msg')):
        if type(value['code']) is not int or value['code'] != 0:
            raise ProtocolError('SQLBot 返回业务错误，code=' + str(value['code']))
        return value['data']
    return value


class HTTPFailure(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__(f'SQLBot HTTP {status}')


class SQLBotClient:
    def __init__(self, config: dict, token: str, transport=None):
        import httpx
        if not token.strip():
            raise ValueError('缺少 SQLBot 访问令牌')
        self.config = config
        self.client = httpx.AsyncClient(
            base_url=config['base_url'].rstrip('/') + '/', follow_redirects=False,
            headers={config.get('token_header', 'X-SQLBOT-TOKEN'): 'Bearer ' + token.removeprefix('Bearer ').strip()},
            timeout=httpx.Timeout(config['timeout_s'], connect=config['connect_timeout_s']), transport=transport)

    async def close(self):
        await self.client.aclose()

    @staticmethod
    def checked(response):
        if not 200 <= response.status_code < 300:
            raise HTTPFailure(response.status_code)

    async def preflight(self):
        response = await self.client.get('datasource/list')
        self.checked(response)
        rows = unwrap_response(response.json())
        if not isinstance(rows, list):
            raise ProtocolError('数据源列表响应格式不符')
        matches = [r for r in rows if r.get('id') == self.config['datasource_id']]
        if len(matches) != 1 or matches[0].get('name') != self.config['datasource_name']:
            raise ProtocolError('数据源 ID/名称不匹配或不可访问')

    async def create_chat(self, question: str) -> int:
        response = await self.client.post('chat/start', json={'question': question, 'datasource': self.config['datasource_id']})
        self.checked(response)
        value = unwrap_response(response.json())
        if not isinstance(value, dict):
            raise ProtocolError('创建会话响应格式不符')
        identifier = value.get('id')
        if type(identifier) is not int or identifier <= 0:
            raise ProtocolError('创建会话未返回有效 id')
        return identifier

    async def ask(self, chat_id: int, request_id: str, question: str):
        payload = {'chat_id': chat_id, 'request_id': request_id, 'question': question}
        async with self.client.stream('POST', 'chat/question', json=payload) as response:
            self.checked(response)
            content_type = response.headers.get('content-type', '')
            if 'application/json' in content_type:
                value = unwrap_response(json.loads(await response.aread()))
                if not isinstance(value, dict):
                    raise ProtocolError('JSON 回放响应格式不符')
                if (value.get('replay') is not True or value.get('chat_id') != chat_id
                        or value.get('request_id') != request_id or value.get('status') not in {'SUCCESS', 'FAILED'}):
                    raise ProtocolError('JSON 回放身份或状态不符')
                yield Event('id', {'id': value['record_id'], 'request_id': request_id})
                if value['status'] == 'FAILED':
                    yield Event('error', {'content': '后端回放失败结果'})
                yield Event('finish', {'replay': True})
                return
            if 'text/event-stream' not in content_type:
                raise ProtocolError('响应不是 SSE 或 JSON 回放')
            lines, ended = [], False
            async for line in response.aiter_lines():
                if line == '':
                    if not lines:
                        continue
                    value = json.loads('\n'.join(lines))
                    lines = []
                    if not isinstance(value, dict) or not isinstance(value.get('type'), str):
                        raise ProtocolError('事件格式错误')
                    if value.get('request_id', request_id) != request_id:
                        raise ProtocolError('事件 request_id 不匹配')
                    kind = value['type']
                    ended = ended or kind in {'finish', 'error'}
                    yield Event(kind, value)
                elif line.startswith('data:'):
                    lines.append(line[5:].lstrip(' '))
            if lines or not ended:
                raise ProtocolError('响应结束但未收到完整的终结事件')
