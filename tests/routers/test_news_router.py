import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.services.news_service as news_service
from app.exceptions.exceptions_handler import add_exception_handlers
from app.routers.news import router

URL = "/api/v1/news"


@pytest.fixture
def news_client() -> TestClient:
    app = FastAPI()
    add_exception_handlers(app)
    app.include_router(router)
    with TestClient(app) as test_client:
        yield test_client


def test_returns_news(news_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    queries = []

    async def fake(query: str) -> list[dict]:
        queries.append(query)
        return [{"title": "t", "contents": "c", "url": "https://news.test/1", "published_at": "2026-11-01T09:00:00+09:00"}]

    monkeypatch.setattr(news_service, "get_region_news", fake)

    response = news_client.post(URL, json={"query": "노원구"})

    assert response.status_code == 200
    assert response.json()[0]["url"] == "https://news.test/1"
    assert queries == ["노원구"]


def test_failure_returns_503(news_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(query: str) -> list[dict]:
        raise ConnectionError("down")

    monkeypatch.setattr(news_service, "get_region_news", boom)

    response = news_client.post(URL, json={"query": "노원구"})

    assert response.status_code == 503
    assert response.json()["success"] is False


def test_query_is_required(news_client: TestClient) -> None:
    assert news_client.post(URL, json={}).status_code == 422
