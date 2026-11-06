import pytest

import app.clients.building_hub as hub
from app.clients.http import ExternalAPIError
from tests.fakes import FakeResponse, FakeSession

PARAMS = {"sigunguCd": "11350", "bjdongCd": "10200", "platGbCd": "0", "bun": "0017", "ji": "0000"}


def ok(items, total: int) -> FakeResponse:
    return FakeResponse({"response": {"body": {"totalCount": str(total), "items": {"item": items} if items is not None else ""}}})


def paged(total: int):
    """totalCount 가 total 인 목록을 pageNo · numOfRows 에 맞게 잘라 주는 응답기"""
    def respond(url: str, params: dict) -> FakeResponse:
        start = (params["pageNo"] - 1) * params["numOfRows"]
        count = max(0, min(params["numOfRows"], total - start))
        return ok([{"n": start + i} for i in range(count)], total)
    return respond


FAULT = FakeResponse(
    {"OpenAPI_ServiceResponse": {"cmmMsgHeader": {"errMsg": "SERVICE ERROR", "returnAuthMsg": "SERVICETIMEOUT_ERROR"}}},
    status_code=503,
)


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> FakeSession:
    fake = FakeSession(ok([], 0))
    monkeypatch.setattr(hub, "_SESSION", fake)
    return fake


def test_adds_key_type_and_paging_params(session: FakeSession) -> None:
    session.responder = ok([{"a": 1}], 1)

    hub.get_titles(PARAMS, max_pages=5)

    call = session.calls[0]
    assert call["url"] == f"{hub.BASE_URL}/getBrTitleInfo"
    assert call["params"] == {
        **PARAMS,
        "serviceKey": "test-bldrgst-key",
        "_type": "json",
        "numOfRows": hub.API_MAX_ROWS,
        "pageNo": 1,
    }
    assert call["timeout"] == 15


@pytest.mark.parametrize(
    ("func", "endpoint"),
    [
        (hub.get_titles, "getBrTitleInfo"),
        (hub.get_expos_pubuse_areas, "getBrExposPubuseAreaInfo"),
        (hub.get_recap_titles, "getBrRecapTitleInfo"),
        (hub.get_atch_jibuns, "getBrAtchJibunInfo"),
    ],
)
def test_endpoint_names(session: FakeSession, func, endpoint: str) -> None:
    func(PARAMS, 1)
    assert session.calls[0]["url"].endswith(f"/{endpoint}")


def test_collects_all_pages_by_total_count(session: FakeSession) -> None:
    session.responder = paged(250)

    rows, total = hub.get_titles(PARAMS, max_pages=5)

    assert total == 250
    assert len(rows) == 250
    assert [c["params"]["pageNo"] for c in session.calls] == [1, 2, 3]


def test_stops_at_max_pages(session: FakeSession) -> None:
    session.responder = paged(6000)

    rows, total = hub.get_expos_pubuse_areas(PARAMS, max_pages=2)

    assert total == 6000
    assert len(rows) == 200
    assert len(session.calls) == 2


def test_single_item_dict_is_wrapped(session: FakeSession) -> None:
    session.responder = ok({"a": 1}, 1)
    assert hub.get_titles(PARAMS, 1) == ([{"a": 1}], 1)


def test_empty_items_string_means_no_rows(session: FakeSession) -> None:
    session.responder = ok(None, 0)
    assert hub.get_titles(PARAMS, 1) == ([], 0)


def test_retries_when_body_is_not_json(session: FakeSession) -> None:
    session.responder = [FakeResponse(text=""), ok([{"a": 1}], 1)]

    rows, _ = hub.get_titles(PARAMS, 1)

    assert rows == [{"a": 1}]
    assert len(session.calls) == 2


def test_fault_payload_raises_after_retries(session: FakeSession) -> None:
    session.responder = FAULT

    with pytest.raises(ExternalAPIError, match="SERVICE ERROR / SERVICETIMEOUT_ERROR"):
        hub.get_titles(PARAMS, 1)
    assert len(session.calls) == hub.JSON_RETRY


def test_throttle_waits_between_calls(session: FakeSession, sleep_calls: list[float], monkeypatch: pytest.MonkeyPatch) -> None:
    #직전 호출 직후에 다시 부르면 CALL_GAP 만큼 기다린다
    monkeypatch.setattr(hub.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(hub, "_last_call", 100.0)

    hub.get_titles(PARAMS, 1)

    assert sleep_calls and sleep_calls[0] == pytest.approx(hub.CALL_GAP)
