import asyncio

import httpx
import pytest

import app.clients.kakao_search as kakao_search
from app.clients.http import ExternalAPIError
from tests.fakes import FakeHttpx, FakeHttpxResponse

DOCS = {"documents": [{"title": "노원 재건축", "url": "https://news.test/1"}]}


def test_async_search_request(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeHttpx(FakeHttpxResponse(DOCS))
    monkeypatch.setattr(httpx, "AsyncClient", fake)

    documents = asyncio.run(kakao_search.search_web("노원구 재건축", size=8))

    assert documents == DOCS["documents"]
    call = fake.calls[0]
    assert call["url"] == kakao_search.WEB_SEARCH_URL
    assert call["params"] == {"query": "노원구 재건축", "sort": "recency", "size": 8}
    assert call["headers"] == {"Authorization": "KakaoAK test-kakao-key"}


def test_async_search_reuses_given_client(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeHttpx(FakeHttpxResponse(DOCS))

    async def run():
        async with fake() as client:
            return await kakao_search.search_web("q", client=client)

    asyncio.run(run())
    #직접 만든 클라이언트 1개만 생기고, 함수 안에서 새로 만들지 않는다
    assert len(fake.client_kwargs) == 1


def test_non_200_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", FakeHttpx(FakeHttpxResponse({"errorType": "x"}, status_code=401)))
    with pytest.raises(ExternalAPIError) as exc:
        asyncio.run(kakao_search.search_web("q"))
    assert exc.value.status_code == 401


def test_missing_documents_key_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", FakeHttpx(FakeHttpxResponse({})))
    assert asyncio.run(kakao_search.search_web("q")) == []


def test_sync_search_uses_explicit_key(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeHttpx(FakeHttpxResponse(DOCS))
    monkeypatch.setattr(httpx, "get", fake.get)

    documents = kakao_search.search_web_sync("q", size=3, api_key="script-key")

    assert documents == DOCS["documents"]
    call = fake.calls[0]
    assert call["headers"] == {"Authorization": "KakaoAK script-key"}
    assert call["params"] == {"query": "q", "sort": "recency", "size": 3}
    assert call["timeout"] == 10
