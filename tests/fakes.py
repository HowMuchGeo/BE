# 테스트용 Fake 객체 모음 — 외부 HTTP · Redis 를 메모리에서 흉내 낸다
import json
from typing import Any, Callable

import requests


#=== requests ===#
class FakeResponse:
    """requests.Response 대역"""

    def __init__(self, json_data: Any = None, status_code: int = 200, text: str | None = None,
                 content_type: str = "application/json") -> None:
        self.status_code = status_code
        self.ok = 200 <= status_code < 400
        self.headers = {"Content-Type": content_type}
        self.text = text if text is not None else json.dumps(json_data, ensure_ascii=False)
        self.content = self.text.encode()

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except ValueError as err:
            raise requests.exceptions.JSONDecodeError(str(err), self.text, 0) from err


class FakeSession:
    """requests.Session 대역.

    responder 가 callable 이면 responder(url, params) 결과를 돌려주고,
    FakeResponse 목록이면 순서대로 돌려준다 (마지막 것은 계속 반복)
    """

    def __init__(self, responder: Callable[[str, dict], FakeResponse] | list[FakeResponse] | FakeResponse) -> None:
        self.responder = responder
        self.calls: list[dict] = []

    def get(self, url: str, params: dict | None = None, timeout: float | None = None) -> FakeResponse:
        params = dict(params or {})
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if callable(self.responder):
            return self.responder(url, params)
        if isinstance(self.responder, list):
            index = min(len(self.calls) - 1, len(self.responder) - 1)
            return self.responder[index]
        return self.responder


#=== httpx ===#
class FakeHttpxResponse:
    """httpx.Response 대역"""

    def __init__(self, json_data: Any = None, status_code: int = 200, text: str | None = None) -> None:
        self.status_code = status_code
        self.text = text if text is not None else json.dumps(json_data, ensure_ascii=False)

    def json(self) -> Any:
        return json.loads(self.text)


class FakeHttpx:
    """httpx.AsyncClient / httpx.get 대역.

    monkeypatch.setattr(httpx, "AsyncClient", fake) 처럼 클래스 자리에 넣어 쓴다.
    client_kwargs 에 AsyncClient(...) 생성 인자, calls 에 요청 기록이 남는다
    """

    def __init__(self, responder: Callable[[str, str, dict], FakeHttpxResponse] | FakeHttpxResponse) -> None:
        self.responder = responder
        self.client_kwargs: list[dict] = []
        self.calls: list[dict] = []

    def _respond(self, method: str, url: str, **kwargs) -> FakeHttpxResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if callable(self.responder):
            return self.responder(method, url, kwargs)
        return self.responder

    #httpx.AsyncClient(...) 자리
    def __call__(self, **kwargs) -> "_FakeAsyncClient":
        self.client_kwargs.append(kwargs)
        return _FakeAsyncClient(self)

    #httpx.get(...) 자리 (동기)
    def get(self, url: str, **kwargs) -> FakeHttpxResponse:
        return self._respond("GET", url, **kwargs)


class _FakeAsyncClient:
    def __init__(self, owner: FakeHttpx) -> None:
        self.owner = owner

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def get(self, url: str, **kwargs) -> FakeHttpxResponse:
        return self.owner._respond("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> FakeHttpxResponse:
        return self.owner._respond("POST", url, **kwargs)


#=== Redis ===#
class FakeRedis:
    """redis.asyncio 클라이언트 대역 (get / set / setex / delete / ping / eval(잠금 해제 스크립트) 만)"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttl: dict[str, int | None] = {}
        self.eval_calls: list[tuple[str, int, tuple]] = []
        self.ping_calls = 0

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool | None:
        if nx and key in self.store:
            return None
        self.store[key] = str(value)
        self.ttl[key] = ex
        return True

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.store[key] = value
        self.ttl[key] = ttl

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if self.store.pop(key, None) is not None:
                self.ttl.pop(key, None)
                removed += 1
        return removed

    async def ping(self) -> bool:
        self.ping_calls += 1
        return True

    #request_lock 의 해제 스크립트만 흉내 낸다 : 값이 같을 때만 DEL
    async def eval(self, script: str, numkeys: int, *args: str) -> int:
        self.eval_calls.append((script, numkeys, args))
        key, token = args[0], args[1]
        if self.store.get(key) == token:
            del self.store[key]
            self.ttl.pop(key, None)
            return 1
        return 0


class BrokenRedis:
    """모든 명령이 실패하는 Redis 대역 (장애 상황)"""

    def __init__(self, error: Exception | None = None) -> None:
        from redis.exceptions import ConnectionError as RedisConnectionError

        self.error = error or RedisConnectionError("redis down")
        self.calls = 0

    def _fail(self, *args, **kwargs):
        self.calls += 1
        raise self.error

    async def get(self, *args, **kwargs):
        self._fail()

    async def set(self, *args, **kwargs):
        self._fail()

    async def setex(self, *args, **kwargs):
        self._fail()

    async def delete(self, *args, **kwargs):
        self._fail()

    async def eval(self, *args, **kwargs):
        self._fail()

    async def ping(self, *args, **kwargs):
        self._fail()
