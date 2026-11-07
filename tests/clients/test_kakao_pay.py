import asyncio

import httpx
import pytest

import app.clients.kakao_pay as kakao_pay
from app.clients.http import ExternalAPIError
from tests.fakes import FakeHttpx, FakeHttpxResponse

READY_ARGS = dict(
    partner_order_id="order-1",
    partner_user_id="7",
    item_name="개인 크레딧 5회권",
    total_amount=500,
    approval_url="http://backend.test/approve",
    cancel_url="http://backend.test/cancel",
    fail_url="http://backend.test/fail",
)


def test_ready_request(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeHttpx(FakeHttpxResponse({"tid": "T1", "next_redirect_pc_url": "https://pay/redirect"}))
    monkeypatch.setattr(httpx, "AsyncClient", fake)

    result = asyncio.run(kakao_pay.ready(**READY_ARGS))

    assert result["tid"] == "T1"
    call = fake.calls[0]
    assert call["method"] == "POST"
    #base url 끝의 "/" 는 제거된다
    assert call["url"] == "https://pay.test/online/v1/payment/ready"
    assert call["headers"] == {
        "Authorization": "SECRET_KEY test-kakaopay-secret",
        "Content-Type": "application/json",
    }
    assert call["json"] == {
        "cid": "TC0ONETIME",
        **READY_ARGS,
        "quantity": 1,
        "tax_free_amount": 0,
    }


def test_approve_request(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeHttpx(FakeHttpxResponse({"tid": "T1", "amount": {"total": 500}}))
    monkeypatch.setattr(httpx, "AsyncClient", fake)

    asyncio.run(kakao_pay.approve(tid="T1", partner_order_id="order-1", partner_user_id="7", pg_token="PG"))

    call = fake.calls[0]
    assert call["url"] == "https://pay.test/online/v1/payment/approve"
    assert call["json"] == {
        "cid": "TC0ONETIME",
        "tid": "T1",
        "partner_order_id": "order-1",
        "partner_user_id": "7",
        "pg_token": "PG",
    }


def test_error_keeps_json_body(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"error_code": -780, "error_message": "approval failure"}
    monkeypatch.setattr(httpx, "AsyncClient", FakeHttpx(FakeHttpxResponse(body, status_code=400)))

    with pytest.raises(ExternalAPIError) as exc:
        asyncio.run(kakao_pay.approve(tid="T1", partner_order_id="o", partner_user_id="7", pg_token="PG"))

    assert exc.value.status_code == 400
    assert exc.value.body == body


def test_error_falls_back_to_text_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", FakeHttpx(FakeHttpxResponse(text="Bad Gateway", status_code=502)))

    with pytest.raises(ExternalAPIError) as exc:
        asyncio.run(kakao_pay.ready(**READY_ARGS))

    assert exc.value.body == "Bad Gateway"
