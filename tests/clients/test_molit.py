import pytest

import app.clients.molit as molit
from app.clients.http import ExternalAPIError
from tests.fakes import FakeResponse, FakeSession

XML = """<?xml version="1.0" encoding="UTF-8"?>
<response><body><items>
  <item><aptNm>미성</aptNm><dealAmount> 85,000 </dealAmount><dealYear>2026</dealYear><dealMonth>9</dealMonth></item>
  <item><aptNm>미륭</aptNm><cdealType>O</cdealType><dealAmount/></item>
</items></body></response>"""


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> FakeSession:
    fake = FakeSession(FakeResponse(text=XML, content_type="application/xml"))
    monkeypatch.setattr(molit, "_SESSION", fake)
    return fake


def test_request_params(session: FakeSession) -> None:
    molit.get_apt_trades("11350", "202609")

    call = session.calls[0]
    assert call["url"] == molit.API_URL
    assert call["params"] == {
        "serviceKey": "test-molit-key",
        "LAWD_CD": "11350",
        "DEAL_YMD": "202609",
        "numOfRows": "1000",
        "pageNo": "1",
    }
    assert call["timeout"] == 30


def test_parses_items_as_stripped_dicts(session: FakeSession) -> None:
    rows = molit.get_apt_trades("11350", "202609")

    assert rows == [
        {"aptNm": "미성", "dealAmount": "85,000", "dealYear": "2026", "dealMonth": "9"},
        {"aptNm": "미륭", "cdealType": "O", "dealAmount": ""},
    ]


def test_explicit_service_key_wins(session: FakeSession) -> None:
    molit.get_apt_trades("11350", "202609", service_key="override")
    assert session.calls[0]["params"]["serviceKey"] == "override"


def test_missing_key_raises_without_calling(session: FakeSession, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MOLIT_API_KEY")
    with pytest.raises(ExternalAPIError, match="MOLIT_API_KEY"):
        molit.get_apt_trades("11350", "202609")
    assert session.calls == []


def test_http_error_raises(session: FakeSession) -> None:
    session.responder = FakeResponse(text="", status_code=503)
    with pytest.raises(ExternalAPIError) as exc:
        molit.get_apt_trades("11350", "202609")
    assert exc.value.status_code == 503


def test_broken_xml_raises(session: FakeSession) -> None:
    session.responder = FakeResponse(text="<response><body>", content_type="application/xml")
    with pytest.raises(ExternalAPIError, match="XML"):
        molit.get_apt_trades("11350", "202609")
