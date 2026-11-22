import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.clients.vworld as vworld
from app.exceptions.exceptions_handler import add_exception_handlers
from app.routers.cadastral import router
from app.services import admin_dong, cadastral_service
from app.services.admin_dong import AdminDongIndex
from app.utils.geo import point_in_box

URL = "/api/v1/cadastral"

#테스트용 행정동 두 개 : 경도 127.000 을 경계로 서쪽 A동 · 동쪽 B동 (각 약 880m x 890m)
DONGS = {
    "dongs": [
        {"code": "1100000001", "name": "A동", "polygons": [[[[126.990, 37.496], [127.000, 37.496], [127.000, 37.504], [126.990, 37.504]]]]},
        {"code": "1100000002", "name": "B동", "polygons": [[[[127.000, 37.496], [127.010, 37.496], [127.010, 37.504], [127.000, 37.504]]]]},
    ]
}

#필지 격자 간격 (도) ≈ 50m
STEP = 0.0005


def _square(x: int, y: int) -> dict:
    x0, y0, x1, y1 = x * STEP, y * STEP, (x + 1) * STEP, (y + 1) * STEP
    return {
        "type": "Feature",
        "id": f"{x}_{y}",
        "properties": {"pnu": f"{x}_{y}"},
        "geometry": {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]},
    }


#BOX 안에 중심이 있는 격자 필지를 돌려주는 가짜 V-World (페이지 크기 그대로 반영)
def _fake_vworld(calls: list):
    async def fake(geom_filter: str, *, page: int = 1, size: int = 1000) -> dict:
        calls.append((geom_filter, page))
        box = cadastral_service.parse_box(geom_filter)
        features = [
            _square(x, y)
            for x in range(int(box[0] // STEP) - 1, int(box[2] // STEP) + 1)
            for y in range(int(box[1] // STEP) - 1, int(box[3] // STEP) + 1)
            if point_in_box((x + 0.5) * STEP, (y + 0.5) * STEP, box) and (x + 0.5) * STEP < box[2] and (y + 0.5) * STEP < box[3]
        ]
        pages = max(1, -(-len(features) // size))
        return {
            "response": {
                "page": {"total": str(pages)},
                "result": {"featureCollection": {"features": features[(page - 1) * size: page * size]}},
            }
        }

    return fake


@pytest.fixture(autouse=True)
def fake_index(monkeypatch: pytest.MonkeyPatch) -> AdminDongIndex:
    index = AdminDongIndex.from_payload(DONGS)
    monkeypatch.setattr(cadastral_service, "get_index", lambda: index)
    return index


@pytest.fixture
def cadastral_client(fake_redis) -> TestClient:
    app = FastAPI()
    add_exception_handlers(app)
    app.include_router(router)
    with TestClient(app) as test_client:
        yield test_client


def _features(response) -> list[dict]:
    return response.json()["response"]["result"]["featureCollection"]["features"]


def test_box_around_is_100m_square() -> None:
    min_lng, min_lat, max_lng, max_lat = cadastral_service.box_around(127.0, 37.5)

    assert (max_lat - min_lat) / 2 == pytest.approx(100 / 111_320, rel=1e-4)
    assert (max_lng - min_lng) / 2 == pytest.approx(100 / (111_320 * 0.79335), rel=1e-3)


def test_center_inside_one_dong_returns_only_that_dong(cadastral_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vworld, "get_cadastral_features", _fake_vworld([]))

    #중심 126.995 → 100m 박스가 A동 안에만 있다
    response = cadastral_client.post(URL, json={"geom_filter": "BOX(126.990,37.495,127.000,37.505)"})

    assert response.status_code == 200
    collection = response.json()["response"]["result"]["featureCollection"]
    assert [d["name"] for d in collection["admin_dongs"]] == ["A동"]
    assert {f["properties"]["adm_nm"] for f in collection["features"]} == {"A동"}
    #A동 880m x 890m / 50m 격자 = 20 x 16 = 320 필지
    assert len(collection["features"]) == 20 * 16


def test_box_crossing_boundary_expands_to_both_dongs(cadastral_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vworld, "get_cadastral_features", _fake_vworld([]))

    #중심 127.0003 → 100m 박스가 경계(127.000)를 넘는다
    response = cadastral_client.post(URL, json={"geom_filter": "BOX(126.9953,37.495,127.0053,37.505)"})

    collection = response.json()["response"]["result"]["featureCollection"]
    assert sorted(d["name"] for d in collection["admin_dongs"]) == ["A동", "B동"]
    names = [f["properties"]["adm_nm"] for f in collection["features"]]
    assert names.count("A동") == 320 and names.count("B동") == 320
    assert len({f["properties"]["pnu"] for f in collection["features"]}) == len(names)


def test_tiles_are_cached(cadastral_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []
    monkeypatch.setattr(vworld, "get_cadastral_features", _fake_vworld(calls))

    body = {"geom_filter": "BOX(126.990,37.495,127.000,37.505)"}
    first = _features(cadastral_client.post(URL, json=body))
    first_calls = len(calls)
    second = _features(cadastral_client.post(URL, json=body))

    assert first_calls > 0
    assert len(calls) == first_calls
    assert len(first) == len(second)


def test_pagination(cadastral_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []
    monkeypatch.setattr(vworld, "get_cadastral_features", _fake_vworld(calls))
    monkeypatch.setattr(cadastral_service, "PAGE_SIZE", 10)

    response = cadastral_client.post(URL, json={"geom_filter": "BOX(126.990,37.495,127.000,37.505)"})

    assert len(_features(response)) == 320
    assert any(page > 1 for _, page in calls)


def test_outside_admin_boundary_keeps_100m_parcels(cadastral_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vworld, "get_cadastral_features", _fake_vworld([]))

    #경계 데이터 밖 (행정동 없음) → 100m 박스와 겹치는 필지만
    response = cadastral_client.post(URL, json={"geom_filter": "BOX(128.0,36.0,128.0,36.0)"})

    collection = response.json()["response"]["result"]["featureCollection"]
    assert collection["admin_dongs"] == []
    assert 0 < len(collection["features"]) <= 6 * 6
    assert all(f["properties"]["adm_cd"] is None for f in collection["features"])


def test_all_tiles_fail_returns_503(cadastral_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(*args, **kwargs) -> dict:
        raise RuntimeError("boom")

    monkeypatch.setattr(vworld, "get_cadastral_features", fake)

    response = cadastral_client.post(URL, json={"geom_filter": "BOX(126.990,37.495,127.000,37.505)"})

    assert response.status_code == 503


@pytest.mark.parametrize("geom", ["", "BOX(1,2,3)", "1,2,3,4", "BOX(a,b,c,d)"])
def test_invalid_box_returns_400(cadastral_client: TestClient, geom: str) -> None:
    response = cadastral_client.post(URL, json={"geom_filter": geom})

    assert response.status_code == 400
    assert response.json()["success"] is False


def test_real_admin_dong_data_loads() -> None:
    index = admin_dong.get_index()

    assert len(index.dongs) > 3000
    assert index.locate(126.9727, 37.5759).name == "서울특별시 종로구 사직동"
