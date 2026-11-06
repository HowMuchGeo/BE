import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.clients.vworld as vworld
from app.exceptions.exceptions_handler import add_exception_handlers
from app.routers.cadastral import router

URL = "/api/v1/cadastral"


@pytest.fixture
def cadastral_client() -> TestClient:
    app = FastAPI()
    add_exception_handlers(app)
    app.include_router(router)
    with TestClient(app) as test_client:
        yield test_client


def test_normalizes_box_and_wraps_feature_collection(cadastral_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    boxes = []

    async def fake(box_str: str) -> dict:
        boxes.append(box_str)
        return {"response": {"result": {"featureCollection": {"type": "FeatureCollection", "features": []}}}}

    monkeypatch.setattr(vworld, "get_cadastral_features", fake)

    response = cadastral_client.post(URL, json={"geom_filter": "BOX( 127.0 , 37.5,127.1, 37.6 )"})

    assert response.status_code == 200
    assert boxes == ["BOX(127.0,37.5,127.1,37.6)"]
    body = response.json()["response"]
    assert body["status"] == "OK"
    assert body["service"]["operation"] == "GetFeature"
    assert body["result"]["featureCollection"] == {"type": "FeatureCollection", "features": []}


def test_raw_feature_collection_is_used_when_not_wrapped(cadastral_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(box_str: str) -> dict:
        return {"type": "FeatureCollection", "features": []}

    monkeypatch.setattr(vworld, "get_cadastral_features", fake)

    response = cadastral_client.post(URL, json={"geom_filter": "BOX(1,2,3,4)"})

    assert response.json()["response"]["result"]["featureCollection"]["type"] == "FeatureCollection"


@pytest.mark.parametrize("geom", ["", "BOX(1,2,3)", "1,2,3,4"])
def test_invalid_box_returns_400(cadastral_client: TestClient, geom: str) -> None:
    response = cadastral_client.post(URL, json={"geom_filter": geom})

    assert response.status_code == 400
    assert response.json()["success"] is False
