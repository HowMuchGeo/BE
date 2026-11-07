import asyncio
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

import app.clients.vworld as vworld
from app.clients.http import ExternalAPIError
from tests.fakes import FakeHttpx, FakeHttpxResponse, FakeResponse, FakeSession

NED = "https://proxy.test/ned/data"
COMMON = {"key": "test-vworld-key", "domain": "test.domain", "format": "json"}


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> FakeSession:
    """vworld._SESSION 교체. 테스트에서 session.responder 로 응답을 정한다"""
    fake = FakeSession(FakeResponse({}))
    monkeypatch.setattr(vworld, "_SESSION", fake)
    return fake


def test_land_characteristics_request(session: FakeSession) -> None:
    session.responder = FakeResponse({"landCharacteristicss": {"field": [{"stdrYear": "2025"}]}})

    rows = vworld.get_land_characteristics("1111")

    assert rows == [{"stdrYear": "2025"}]
    call = session.calls[0]
    assert call["url"] == f"{NED}/getLandCharacteristics"
    assert call["params"] == {**COMMON, "pnu": "1111", "numOfRows": 30, "pageNo": 1}
    assert call["timeout"] == 5


def test_single_field_dict_is_wrapped_in_list(session: FakeSession) -> None:
    session.responder = FakeResponse({"indvdLandPrices": {"field": {"stdrYear": "2025", "pblntfPclnd": "1000"}}})
    assert vworld.get_indvd_land_prices("1111") == [{"stdrYear": "2025", "pblntfPclnd": "1000"}]


def test_missing_root_returns_empty_list(session: FakeSession) -> None:
    session.responder = FakeResponse({"somethingElse": {}})
    assert vworld.get_land_characteristics("1111") == []


def test_land_price_has_no_call_gap_but_others_do(session: FakeSession, sleep_calls: list[float]) -> None:
    session.responder = FakeResponse({"indvdLandPrices": {"field": []}})
    vworld.get_indvd_land_prices("1111")
    assert sleep_calls == []

    session.responder = FakeResponse({"landCharacteristicss": {"field": []}})
    vworld.get_land_characteristics("1111")
    assert sleep_calls == [vworld.CALL_GAP]


def test_possessions_returns_rows_and_total(session: FakeSession) -> None:
    session.responder = FakeResponse({"possessions": {"totalCount": "4679", "field": [{"posesnSeCodeNm": "개인"}]}})

    rows, total = vworld.get_possessions("1111", rows=20)

    assert rows == [{"posesnSeCodeNm": "개인"}]
    assert total == 4679
    assert session.calls[0]["params"]["numOfRows"] == 20


def test_possessions_total_is_none_when_absent(session: FakeSession) -> None:
    session.responder = FakeResponse({"possessions": {"totalCount": "", "field": []}})
    assert vworld.get_possessions("1111", rows=20) == ([], None)


def test_apart_housing_prices_finds_root_with_field(session: FakeSession) -> None:
    session.responder = FakeResponse({"apartHousingPrices": {"field": [{"hoNm": "101"}], "totalCount": "1"}})

    rows = vworld.get_apart_housing_prices("1111", 2026, page=3, rows=1000)

    assert rows == [{"hoNm": "101"}]
    call = session.calls[0]
    assert call["url"] == f"{NED}/getApartHousingPriceAttr"
    assert call["params"] == {**COMMON, "pnu": "1111", "stdrYear": "2026", "numOfRows": 1000, "pageNo": 3}
    assert call["timeout"] == 10


def test_indvd_housing_prices_request(session: FakeSession) -> None:
    session.responder = FakeResponse({"indvdHousingPrices": {"field": []}})

    vworld.get_indvd_housing_prices("1111", 2025)

    assert session.calls[0]["params"] == {**COMMON, "pnu": "1111", "stdrYear": "2025", "numOfRows": 20, "pageNo": 1}


@pytest.mark.parametrize(
    "response",
    [FakeResponse({}, status_code=500), FakeResponse(text="<html/>", content_type="text/html")],
    ids=["http-500", "non-json"],
)
def test_bad_response_raises(session: FakeSession, response: FakeResponse) -> None:
    session.responder = response
    with pytest.raises(ExternalAPIError):
        vworld.get_land_characteristics("1111")


class TestCadastral:
    def test_request_uses_urlencode_and_keeps_tls_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeHttpx(FakeHttpxResponse({"response": {"result": {"featureCollection": {"type": "FeatureCollection"}}}}))
        monkeypatch.setattr(httpx, "AsyncClient", fake)

        data = asyncio.run(vworld.get_cadastral_features("BOX(127.0,37.5,127.1,37.6)"))

        assert data["response"]["result"]["featureCollection"]["type"] == "FeatureCollection"
        assert fake.client_kwargs == [{"verify": False}]

        url = fake.calls[0]["url"]
        assert url.startswith("https://proxy.test/req/data?")
        #괄호·쉼표가 퍼센트 인코딩된다 (기존 urlencode 방식)
        assert "geomFilter=BOX%28127.0%2C37.5%2C127.1%2C37.6%29" in url
        query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        assert query["data"] == "lp_pa_cbnd_bubun"
        assert query["key"] == "test-vworld-key"
        assert query["size"] == "1000"
        assert fake.calls[0]["timeout"] == 5.0

    def test_non_json_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(httpx, "AsyncClient", FakeHttpx(FakeHttpxResponse(text="not json", status_code=502)))
        with pytest.raises(ExternalAPIError) as exc:
            asyncio.run(vworld.get_cadastral_features("BOX(1,2,3,4)"))
        assert exc.value.status_code == 502
