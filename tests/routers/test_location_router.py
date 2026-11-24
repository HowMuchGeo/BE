import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.clients import kakao_search
from app.exceptions.exceptions_handler import add_exception_handlers
from app.routers.location_transformation import router
from app.services.location_service import strip_detail

URL = "/api/v1/location"

ADDRESS_DOC = {
    "address_name": "서울 강남구 테헤란로 152",
    "x": "127.036508620542",
    "y": "37.5000242405515",
    "address": {"address_name": "서울 강남구 역삼동 737", "b_code": "1168010100"},
    "road_address": {"address_name": "서울 강남구 테헤란로 152", "building_name": "강남파이낸스센터"},
}


@pytest.fixture
def client(fake_redis) -> TestClient:
    app = FastAPI()
    add_exception_handlers(app)
    app.include_router(router)
    with TestClient(app) as test_client:
        yield test_client


def _fake(results: dict[str, list[dict]], calls: list):
    async def fake(query: str, size: int = 1, client=None, api_key=None) -> list[dict]:
        calls.append(query)
        return results.get(query, [])
    return fake


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("서울 강남구 테헤란로 152", "서울 강남구 테헤란로 152"),
        ("서울 강남구 테헤란로 152, 101동 1203호", "서울 강남구 테헤란로 152"),
        ("서울 강남구 테헤란로 152 (역삼동) 12층", "서울 강남구 테헤란로 152"),
        ("서울 강남구 역삼1동 737 B동 302호실", "서울 강남구 역삼1동 737"),
    ],
)
def test_strip_detail(address: str, expected: str) -> None:
    assert strip_detail(address) == expected


def test_address_match(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []
    monkeypatch.setattr(kakao_search, "search_address", _fake({"서울 강남구 테헤란로 152": [ADDRESS_DOC]}, calls))

    response = client.post(URL, json={"address": "  서울  강남구 테헤란로 152 "})

    assert response.status_code == 200
    body = response.json()
    assert body["latitude"] == pytest.approx(37.5000242405515)
    assert body["longitude"] == pytest.approx(127.036508620542)
    assert body["b_code"] == "1168010100"
    assert body["match_type"] == "address"
    assert body["query"] == "서울 강남구 테헤란로 152"


def test_retry_without_detail(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []
    monkeypatch.setattr(kakao_search, "search_address", _fake({"서울 강남구 테헤란로 152": [ADDRESS_DOC]}, calls))

    response = client.post(URL, json={"address": "서울 강남구 테헤란로 152, 101동 1203호"})

    assert response.status_code == 200
    assert calls == ["서울 강남구 테헤란로 152, 101동 1203호", "서울 강남구 테헤란로 152"]


def test_keyword_fallback(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kakao_search, "search_address", _fake({}, []))
    place = {"place_name": "은마아파트", "address_name": "서울 강남구 대치동 316", "road_address_name": "서울 강남구 삼성로 212", "x": "127.0626", "y": "37.4994"}
    monkeypatch.setattr(kakao_search, "search_keyword", _fake({"은마아파트": [place]}, []))

    response = client.post(URL, json={"address": "은마아파트"})

    assert response.status_code == 200
    assert response.json()["match_type"] == "keyword"
    assert response.json()["building_name"] == "은마아파트"


def test_not_found(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kakao_search, "search_address", _fake({}, []))
    monkeypatch.setattr(kakao_search, "search_keyword", _fake({}, []))

    assert client.post(URL, json={"address": "없는주소 12345"}).status_code == 404


def test_external_error(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(*args, **kwargs):
        raise RuntimeError("kakao down")
    monkeypatch.setattr(kakao_search, "search_address", broken)

    assert client.post(URL, json={"address": "서울 강남구 테헤란로 152"}).status_code == 503


def test_invalid_address(client: TestClient) -> None:
    assert client.post(URL, json={"address": " "}).status_code == 422
