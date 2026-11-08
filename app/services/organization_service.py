import secrets
import string
from datetime import datetime, timezone
from typing import Literal
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.models.organization import Organization, OrganizationMembership

#사용자 역할. 모든 계정은 개인 계정으로 가입하며, 역할은 조합 운영·소속 여부로 결정된다
Role = Literal["personal", "leader", "member"]

#조직의 플랜이 활성 상태이며 사용 기간이 남아있는지 확인
def is_organization_active(organization: Organization) -> bool:
    return (
        organization.status == "active"
        and organization.paid_until is not None
        and organization.paid_until > datetime.now(timezone.utc)
    )

#사용자가 조합장인 조직 조회
def get_led_organization(session: Session, user_id: int, *, for_update: bool = False) -> Organization | None:
    stmt = select(Organization).where(Organization.leader_user_id == user_id)
    if for_update:
        stmt = stmt.with_for_update()
    return session.execute(stmt).scalar_one_or_none()

#사용자의 조직 멤버십 조회 (상태 무관)
def get_membership(session: Session, user_id: int) -> OrganizationMembership | None:
    return session.execute(
        select(OrganizationMembership).where(OrganizationMembership.user_id == user_id)
    ).scalar_one_or_none()

#사용자 역할 반환
def get_role(session: Session, user_id: int) -> Role:
    if get_led_organization(session, user_id):
        return "leader"
    membership = get_membership(session, user_id)
    if membership and membership.status == "active":
        return "member"
    return "personal"

#사용자가 이용 가능한 활성 조직 반환 (조합장 우선, 없으면 활성 멤버십 조직)
def get_active_organization(session: Session, user_id: int) -> Organization | None:
    organization = get_led_organization(session, user_id)
    if organization:
        return organization if is_organization_active(organization) else None

    row = session.execute(
        select(Organization)
        .join(OrganizationMembership, Organization.organization_id == OrganizationMembership.organization_id)
        .where(
            OrganizationMembership.user_id == user_id,
            OrganizationMembership.status == "active",
        )
    ).scalar_one_or_none()

    return row if row and is_organization_active(row) else None

#활성 조합에 소속(조합장 포함)되어 무제한 크레딧 대상인지 확인
def has_unlimited_credits(session: Session, user_id: int) -> bool:
    return get_active_organization(session, user_id) is not None

#조직의 활성 구성원 수
def count_active_members(session: Session, organization_id: int) -> int:
    return session.scalar(
        select(func.count()).select_from(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.status == "active",
        )
    ) or 0

_INVITATION_ALPHABET = string.ascii_uppercase + string.digits

#조합 초대코드 생성 (영문 대문자·숫자 12자리)
def generate_invitation_code() -> str:
    return "".join(secrets.choice(_INVITATION_ALPHABET) for _ in range(12))
