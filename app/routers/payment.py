import logging
import secrets
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.clients import kakao_pay
from app.clients.http import ExternalAPIError
from app.config.settings import settings
from app.database.database_connection import get_db
from app.exceptions.exceptions_handler import BadRequestException, ServiceUnavailableException, UnauthorizedException
from app.models.payment import PaymentOrder
from app.schemas.payment.payment_request import PaymentReadyRequest
from app.schemas.payment.payment_response import PaymentReadyResponse, ProductResponse
from app.services.payment_service import fulfill_order, get_product, list_products, validate_purchase

#결제 라우터 (크레딧 충전 · 조합 플랜 통합)
router = APIRouter(
    prefix="/api/v1/payments",
    tags=["Payment Router"]
)

#백엔드 Logger
logger = logging.getLogger(__name__)

#콜백·리다이렉트 주소 (끝 "/" 는 settings 에서 제거된다). 필수값 검증은 서버 기동 시 app/main.py 에서 한다
BACKEND_URL = settings.backend_url
FRONTEND_URL = settings.frontend_url

#상품 종류별 결제 결과 프론트 페이지
RESULT_PAGES = {
    "credit": "/credits/payment",
    "plan": "/payment",
}

#결제 결과 페이지로 리다이렉트
def _redirect(product_type: str | None, status: str, **params: str | int) -> RedirectResponse:
    path = RESULT_PAGES.get(product_type or "", RESULT_PAGES["plan"])
    query = "&".join(f"{key}={value}" for key, value in {"status": status, **params}.items())
    return RedirectResponse(url=f"{FRONTEND_URL}{path}?{query}", status_code=303)

#주문 조회
def _get_order(session: Session, partner_order_id: str, *, for_update: bool = False) -> PaymentOrder | None:
    stmt = select(PaymentOrder).where(PaymentOrder.partner_order_id == partner_order_id)
    if for_update:
        stmt = stmt.with_for_update()
    return session.execute(stmt).scalar_one_or_none()

#결제 가능한 상품 목록 API 엔드포인트
@router.get("/products", response_model=list[ProductResponse])
def get_products():
    return [
        ProductResponse(
            product_code=product.code,
            product_type=product.type,
            name=product.name,
            price=product.price,
            credit_amount=product.credit_amount,
            max_members=product.plan.max_members if product.plan else None,
            duration_months=product.plan.duration_months if product.plan else None,
        )
        for product in list_products()
    ]

#결제 준비 API 엔드포인트
@router.post("/ready", response_model=PaymentReadyResponse)
async def payment_ready(body: PaymentReadyRequest, request: Request, session: Session = Depends(get_db)):

    #로그인된 사용자인지 확인
    user_id = request.session.get("user_id")
    if user_id is None:
        raise UnauthorizedException("로그인이 필요합니다.")
    user_id = int(user_id)

    #상품 조회 및 구매 가능 여부 검증
    product = get_product(body.product_code)
    if product is None:
        raise BadRequestException("유효하지 않은 상품입니다.")
    validate_purchase(session, user_id, product)

    #카카오페이 결제 준비 요청
    partner_order_id = secrets.token_urlsafe(24)
    callback_base = f"{BACKEND_URL}/api/v1/payments"
    try:
        result = await kakao_pay.ready(
            partner_order_id=partner_order_id,
            partner_user_id=str(user_id),
            item_name=product.name,
            total_amount=product.price,
            approval_url=f"{callback_base}/approve?partner_order_id={partner_order_id}",
            cancel_url=f"{callback_base}/cancel?partner_order_id={partner_order_id}",
            fail_url=f"{callback_base}/fail?partner_order_id={partner_order_id}",
        )
    except ExternalAPIError as err:
        logger.warning("[ Log ] : KakaoPay 결제 준비 실패 (%s) : %s", product.code, err.body)
        raise ServiceUnavailableException("결제를 시작하지 못했습니다.")

    if not result.get("tid") or not result.get("next_redirect_pc_url"):
        raise ServiceUnavailableException("카카오페이 결제 준비 응답이 올바르지 않습니다.")

    #주문 저장 (상품 정보 스냅샷)
    session.add(PaymentOrder(
        partner_order_id=partner_order_id,
        user_id=user_id,
        product_type=product.type,
        product_code=product.code,
        item_name=product.name,
        amount=product.price,
        credit_amount=product.credit_amount,
        tid=result["tid"],
        status="ready",
    ))
    session.commit()

    return PaymentReadyResponse(
        next_redirect_pc_url=result["next_redirect_pc_url"],
        tid=result["tid"],
        product_code=product.code,
        product_type=product.type,
        item_name=product.name,
        price=product.price,
        credit_amount=product.credit_amount,
    )

#결제 승인 API 엔드포인트 (카카오페이 approval_url 콜백)
@router.get("/approve")
async def payment_approve(
    pg_token: str = Query(...),
    partner_order_id: str = Query(...),
    session: Session = Depends(get_db),
):
    #주문 조회 (중복 승인 방지를 위해 행 잠금)
    order = _get_order(session, partner_order_id, for_update=True)
    if order and order.status == "approved":
        #새로고침 등으로 콜백이 다시 들어온 경우 결과 페이지만 다시 보여준다
        return _redirect(order.product_type, "success")
    if not order or not order.tid or order.status != "ready":
        return _redirect(order.product_type if order else None, "fail")

    #카카오페이 승인 요청
    try:
        approval = await kakao_pay.approve(
            tid=order.tid,
            partner_order_id=order.partner_order_id,
            partner_user_id=str(order.user_id),
            pg_token=pg_token,
        )
    except ExternalAPIError as err:
        logger.warning("[ Log ] : KakaoPay 결제 승인 실패 (%s) : %s", order.partner_order_id, err.body)
        order.status = "failed"
        session.commit()
        return _redirect(order.product_type, "fail")

    #승인 응답이 주문 내용과 일치하는지 검증
    paid_total = (approval.get("amount") or {}).get("total")
    if (
        approval.get("tid") != order.tid
        or approval.get("partner_order_id") != order.partner_order_id
        or int(paid_total or 0) != order.amount
    ):
        logger.error("[ Log ] : KakaoPay 승인 정보 불일치 : %s", order.partner_order_id)
        raise ServiceUnavailableException("결제 승인 정보가 주문 내용과 일치하지 않습니다.")

    #상품 지급 (크레딧 충전 또는 조합 생성·연장) 후 주문 확정
    await fulfill_order(session, order)
    order.status = "approved"
    order.approved_at = datetime.now(timezone.utc)
    session.commit()

    if order.product_type == "credit":
        return _redirect("credit", "success", credits=order.credit_amount)
    return _redirect("plan", "success")

#결제 취소 API 엔드포인트
@router.get("/cancel")
def payment_cancel(partner_order_id: str = Query(...), session: Session = Depends(get_db)):
    order = _close_ready_order(session, partner_order_id, "cancelled")
    return _redirect(order.product_type if order else None, "cancel")

#결제 실패 API 엔드포인트
@router.get("/fail")
def payment_fail(partner_order_id: str = Query(...), session: Session = Depends(get_db)):
    order = _close_ready_order(session, partner_order_id, "failed")
    return _redirect(order.product_type if order else None, "fail")

#대기(ready) 중인 주문을 취소·실패 상태로 종료
def _close_ready_order(session: Session, partner_order_id: str, new_status: str) -> PaymentOrder | None:
    order = _get_order(session, partner_order_id)
    if order and order.status == "ready":
        order.status = new_status
        session.commit()
    return order
