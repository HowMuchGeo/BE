import asyncio

import httpx
import pytest

import app.clients.kakao_search as kakao_search
import app.services.news_service as news_service
from app.clients.http import ExternalAPIError
from tests.fakes import FakeHttpx, FakeHttpxResponse


def doc(url: str, title: str = "<b>노원</b> 재건축 &amp; 소식", contents: str = "<em>내용</em>") -> dict:
    return {"url": url, "title": title, "contents": contents, "datetime": "2026-11-01T09:00:00.000+09:00"}


@pytest.fixture(autouse=True)
def no_real_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", FakeHttpx(FakeHttpxResponse({})))


@pytest.fixture
def search(monkeypatch: pytest.MonkeyPatch):
    """키워드별 검색 결과를 등록한다. 값이 Exception 이면 그 예외를 던진다"""
    results: dict = {}
    calls: list = []

    async def fake_search(query, size=10, sort="recency", client=None, api_key=None):
        calls.append({"query": query, "size": size, "client": client})
        result = results.get(query, [])
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(kakao_search, "search_web", fake_search)
    results["_calls"] = calls
    return results


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, ""), ("", ""), ("<b>a</b> &amp; b", "a & b"), ("plain", "plain")],
)
def test_clean_html(raw, expected) -> None:
    assert news_service.clean_html(raw) == expected


def test_searches_redevelopment_then_reconstruction(search) -> None:
    asyncio.run(news_service.get_region_news("노원구"))

    calls = search["_calls"]
    assert [c["query"] for c in calls] == ["노원구 재개발", "노원구 재건축"]
    assert all(c["size"] == news_service.NEWS_SIZE for c in calls)
    #두 검색이 같은 클라이언트를 쓴다
    assert calls[0]["client"] is calls[1]["client"]


def test_cleans_and_maps_documents(search) -> None:
    search["노원구 재개발"] = [doc("https://news.test/1")]

    news = asyncio.run(news_service.get_region_news("노원구"))

    assert news == [{
        "title": "노원 재건축 & 소식",
        "contents": "내용",
        "url": "https://news.test/1",
        "published_at": "2026-11-01T09:00:00.000+09:00",
    }]


def test_filters_follow_existing_rules(search) -> None:
    #재개발 검색 : 나무위키 · 디시인사이드 제외 / 재건축 검색 : 나무위키만 제외 (기존 동작)
    search["노원구 재개발"] = [
        doc("https://namu.wiki/w/x"),
        doc("https://gall.dcinside.com/board/1"),
        doc("https://news.test/a"),
        doc("https://news.test/pdf", title="보고서.pdf"),
    ]
    search["노원구 재건축"] = [
        doc("https://namu.wiki/w/y"),
        doc("https://gall.dcinside.com/board/2"),
        doc("https://news.test/b"),
    ]

    urls = [n["url"] for n in asyncio.run(news_service.get_region_news("노원구"))]

    assert urls == ["https://news.test/a", "https://gall.dcinside.com/board/2", "https://news.test/b"]


def test_document_without_url_is_kept(search) -> None:
    search["노원구 재건축"] = [{"title": "t", "contents": "c", "url": None, "datetime": None}]
    assert len(asyncio.run(news_service.get_region_news("노원구"))) == 1


def test_one_failed_search_is_skipped(search) -> None:
    search["노원구 재개발"] = ExternalAPIError("kakao-web-search", "HTTP 500", status_code=500)
    search["노원구 재건축"] = [doc("https://news.test/b")]

    news = asyncio.run(news_service.get_region_news("노원구"))

    assert [n["url"] for n in news] == ["https://news.test/b"]


def test_network_error_propagates(search) -> None:
    search["노원구 재개발"] = ConnectionError("down")
    with pytest.raises(ConnectionError):
        asyncio.run(news_service.get_region_news("노원구"))
