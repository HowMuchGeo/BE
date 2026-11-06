import httpx
from app.clients.http import ExternalAPIError
from app.config.settings import settings

def _headers() -> dict:
    return {
        "Authorization": f"SECRET_KEY {settings.kakao_payment_secret_key}",
        "Content-Type": "application/json",
    }

#응답 본문 (JSON 이 아니면 텍스트)
def _body(response: httpx.Response):
    try:
        return response.json()
    except ValueError:
        return response.text

async def _post(endpoint: str, payload: dict) -> dict:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{settings.kakao_payment_base_url}/{endpoint}",
            headers=_headers(),
            json={"cid": settings.kakao_payment_cid, **payload},
        )
    if response.status_code != 200:
        raise ExternalAPIError(
            f"kakao-pay-{endpoint}",
            f"HTTP {response.status_code}",
            status_code=response.status_code,
            body=_body(response),
        )
    return response.json()

#결제 준비
async def ready(
    *,
    partner_order_id: str,
    partner_user_id: str,
    item_name: str,
    total_amount: int,
    approval_url: str,
    cancel_url: str,
    fail_url: str,
    quantity: int = 1,
    tax_free_amount: int = 0,
) -> dict:
    return await _post("ready", {
        "partner_order_id": partner_order_id,
        "partner_user_id": partner_user_id,
        "item_name": item_name,
        "quantity": quantity,
        "total_amount": total_amount,
        "tax_free_amount": tax_free_amount,
        "approval_url": approval_url,
        "cancel_url": cancel_url,
        "fail_url": fail_url,
    })

#결제 승인
async def approve(*, tid: str, partner_order_id: str, partner_user_id: str, pg_token: str) -> dict:
    return await _post("approve", {
        "tid": tid,
        "partner_order_id": partner_order_id,
        "partner_user_id": partner_user_id,
        "pg_token": pg_token,
    })