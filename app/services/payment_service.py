from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from sqlalchemy.orm import Session
from app.exceptions.exceptions_handler import BadRequestException
from app.models.organization import Organization
from app.models.payment import PaymentOrder
from app.services.credit_service import fulfill_credit_purchase
from app.services.organization_service import (
    count_active_members,
    generate_invitation_code,
    get_led_organization,
    get_membership,
    has_unlimited_credits,
)
from app.services.plan_service import PLANS, Plan, add_plan_duration, get_plan

ProductType = Literal["credit", "plan"]

#결제 가능한 상품
@dataclass(frozen=True)
class Product:
    code: str
    type: ProductType
    name: str
    price: int
    credit_amount: int | None = None
    plan: Plan | None = None

#개인 크레딧 상품
CREDIT_PRODUCTS: dict[str, Product] = {
    "CREDIT_5": Product("CREDIT_5", "credit", "개인 크레딧 5회권", 500, credit_amount=5),
}

#조합 플랜 상품 (plan_service.PLANS 를 상품으로 노출)
PLAN_PRODUCTS: dict[str, Product] = {
    plan.code: Product(plan.code, "plan", f"조합 {plan.name} 플랜", plan.price, plan=plan)
    for plan in PLANS.values()
}

#상품 조회
def get_product(product_code: str) -> Product | None:
    code = product_code.strip()
    if code.upper() in CREDIT_PRODUCTS:
        return CREDIT_PRODUCTS[code.upper()]
    plan = get_plan(code)
    return PLAN_PRODUCTS.get(plan.code) if plan else None

#전체 상품 목록
def list_products() -> list[Product]:
    return [*CREDIT_PRODUCTS.values(), *PLAN_PRODUCTS.values()]

#결제 시작 전 구매 가능 여부 검증. 불가하면 BadRequestException
def validate_purchase(session: Session, user_id: int, product: Product) -> None:
    if product.type == "credit":
        if has_unlimited_credits(session, user_id):
            raise BadRequestException("활성 조합 플랜 이용자는 개인 크레딧을 구매할 필요가 없습니다.")
        return

    #조합 구성원(신청 대기 포함)은 다른 조합을 만들 수 없다
    if get_membership(session, user_id):
        raise BadRequestException("조합에 가입(신청)된 계정은 조합 플랜을 결제할 수 없습니다. 조합 탈퇴 후 이용해 주세요.")

    #이미 조합장이면 갱신(또는 플랜 변경) 결제. 현재 구성원 수가 새 플랜 한도를 넘으면 안 된다
    organization = get_led_organization(session, user_id)
    if organization and organization.status != "active":
        raise BadRequestException("현재 결제할 수 없는 조합 상태입니다.")
    if organization and count_active_members(session, organization.organization_id) > product.plan.max_members:
        raise BadRequestException("현재 구성원 수가 선택한 플랜의 최대 인원을 초과합니다.")

#결제 승인 후 상품 지급
async def fulfill_order(session: Session, order: PaymentOrder) -> None:
    if order.product_type == "credit":
        await fulfill_credit_purchase(order.user_id, order.partner_order_id, order.credit_amount)
        return

    plan = get_plan(order.product_code)
    if plan is None:
        raise BadRequestException("결제한 조합 플랜 정보를 찾을 수 없습니다.")
    order.organization_id = _activate_plan(session, order.user_id, plan).organization_id

#조합 생성 또는 기간 연장. 첫 플랜 결제 시 사용자는 조합장이 된다
def _activate_plan(session: Session, user_id: int, plan: Plan) -> Organization:
    now = datetime.now(timezone.utc)
    organization = get_led_organization(session, user_id, for_update=True)

    if organization is None:
        organization = Organization(
            leader_user_id=user_id,
            plan_code=plan.code,
            invitation_code=generate_invitation_code(),
            status="active",
            max_members=plan.max_members,
            paid_until=add_plan_duration(now, plan.duration_months),
        )
        session.add(organization)
        session.flush()
        return organization

    start = organization.paid_until if organization.paid_until and organization.paid_until > now else now
    organization.paid_until = add_plan_duration(start, plan.duration_months)
    organization.plan_code = plan.code
    organization.max_members = plan.max_members
    organization.status = "active"
    return organization
