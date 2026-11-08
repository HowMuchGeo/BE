from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database.database_connection import get_db
from app.exceptions.exceptions_handler import BadRequestException, ConflictException, UnauthorizedException
from app.models.organization import Organization, OrganizationMembership
from app.models.organization_scenario import OrganizationScenario
from app.models.user import User
from app.services.plan_service import get_plan
from app.services.organization_service import (
    count_active_members,
    get_led_organization,
    get_membership,
    is_organization_active,
)
from app.schemas.user.user_request import JoinOrganizationRequest
from app.schemas.organization_scenario import OrganizationScenarioCreate

#조직 라우터
router = APIRouter(
    prefix="/api/v1/organization",
    tags=["Organization Router"]
)

#로그인 된 사용자인지 확인하는 함수
def _require_user_id(request: Request) -> int:
    user_id = request.session.get("user_id")
    if user_id is None:
        raise UnauthorizedException("로그인이 필요합니다.")
    return int(user_id)

#활성 플랜을 가진 조합장의 조직 반환. 아니면 Bad Request Exception
def _require_active_leader_organization(session: Session, user_id: int, action: str) -> Organization:
    organization = get_led_organization(session, user_id)
    if not organization:
        raise BadRequestException(f"조합장만 {action}할 수 있습니다.")
    if not is_organization_active(organization):
        raise BadRequestException("활성 조합 플랜이 필요합니다.")
    return organization

#유효한 조직에서 시나리오 조회 가능하게 하는 함수
def _get_accessible_scenario_organization(session: Session, user_id: int) -> Organization:
    organization = get_led_organization(session, user_id)
    if organization:
        if not is_organization_active(organization):
            raise BadRequestException("활성 조합 플랜이 필요합니다.")
        return organization

    membership = get_membership(session, user_id)
    organization = (
        session.get(Organization, membership.organization_id)
        if membership and membership.status == "active"
        else None
    )
    if not organization or not is_organization_active(organization):
        raise BadRequestException("활성 조합에 가입된 구성원만 조합 자료를 볼 수 있습니다.")
    return organization

#조직 시나리오 묶음 조회 함수
@router.get("/scenarios")
def get_organization_scenarios(request: Request, session: Session = Depends(get_db)):
    user_id = _require_user_id(request)
    organization = _get_accessible_scenario_organization(session, user_id)
    scenarios = session.execute(
        select(OrganizationScenario)
        .where(OrganizationScenario.organization_id == organization.organization_id)
        .order_by(OrganizationScenario.updated_at.desc(), OrganizationScenario.scenario_id.desc())
    ).scalars().all()

    return {
        "scenarios": [
            {
                "id": scenario.scenario_id,
                "name": scenario.scenario_name,
                "updated_at": scenario.updated_at.isoformat(),
            }
            for scenario in scenarios
        ],
    }

#시나리오 불러오기 API 엔드포인트
@router.get("/scenarios/{scenario_id}")
def get_organization_scenario(
    scenario_id: int,
    request: Request,
    session: Session = Depends(get_db),
):
    user_id = _require_user_id(request)
    organization = _get_accessible_scenario_organization(session, user_id)

    scenario = session.execute(
        select(OrganizationScenario).where(
            OrganizationScenario.scenario_id == scenario_id,
            OrganizationScenario.organization_id == organization.organization_id,
        )
    ).scalar_one_or_none()
    if not scenario:
        raise BadRequestException("조합 기준안을 찾을 수 없습니다.")

    return {
        "scenario": scenario.scenario_data,
        "updated_at": scenario.updated_at.isoformat(),
    }

#시나리오 생성 API 엔드포인트
@router.post("/scenarios", status_code=status.HTTP_201_CREATED)
def save_organization_scenario(
    body: OrganizationScenarioCreate,
    request: Request,
    session: Session = Depends(get_db),
):
    user_id = _require_user_id(request)
    organization = _require_active_leader_organization(session, user_id, "조합 기준안을 저장")

    scenario = OrganizationScenario(
        organization_id=organization.organization_id,
        scenario_name=body.name,
        scenario_data=body.scenario.model_dump(mode="json"),
        updated_by_user_id=user_id,
    )
    session.add(scenario)
    session.commit()
    session.refresh(scenario)
    return {
        "id": scenario.scenario_id,
        "name": scenario.scenario_name,
        "scenario": scenario.scenario_data,
        "updated_at": scenario.updated_at.isoformat(),
    }

#시나리오 삭제 API 엔드포인트
@router.delete("/scenarios/{scenario_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_organization_scenario(
    scenario_id: int,
    request: Request,
    session: Session = Depends(get_db),
):
    user_id = _require_user_id(request)
    organization = _require_active_leader_organization(session, user_id, "조합 기준안을 삭제")

    scenario = session.execute(
        select(OrganizationScenario).where(
            OrganizationScenario.scenario_id == scenario_id,
            OrganizationScenario.organization_id == organization.organization_id,
        )
    ).scalar_one_or_none()
    if not scenario:
        raise BadRequestException("조합 기준안을 찾을 수 없습니다.")
    session.delete(scenario)
    session.commit()

#가입된 조직 확인 API 엔드포인트
@router.get("/me")
def get_my_organization(request: Request, session: Session = Depends(get_db)):

    #로그인이 되어있는지 확인
    user_id = _require_user_id(request)

    #사용자가 조합장이라면 반환
    organization = get_led_organization(session, user_id)
    if organization:
        plan = get_plan(organization.plan_code)
        active = is_organization_active(organization)
        return {
            "role": "leader",
            "organization_id": organization.organization_id,
            "plan_code": organization.plan_code,
            "plan_name": plan.name if plan else organization.plan_code,
            "price": plan.price if plan else None,
            "duration_months": plan.duration_months if plan else None,
            "invitation_code": organization.invitation_code if active else None,
            "organization_status": organization.status if active else "expired",
            "paid_until": organization.paid_until.isoformat() if organization.paid_until else None,
            "max_members": organization.max_members,
            "unlimited_credits": active,
        }

    #사용자가 조합 구성원(또는 가입 신청자)이라면 반환
    membership = get_membership(session, user_id)
    if membership:
        organization = session.get(Organization, membership.organization_id)
        plan = get_plan(organization.plan_code) if organization else None
        leader = session.get(User, organization.leader_user_id) if organization else None
        unlimited = bool(organization and membership.status == "active" and is_organization_active(organization))
        return {
            "role": "member",
            "organization_id": organization.organization_id if organization else None,
            "membership_status": membership.status,
            "organization_name": leader.user_name if leader else None,
            "plan_name": plan.name if plan else None,
            "price": plan.price if plan else None,
            "duration_months": plan.duration_months if plan else None,
            "paid_until": organization.paid_until.isoformat() if organization and organization.paid_until else None,
            "unlimited_credits": unlimited,
        }

    #소속 없는 개인 계정
    return {
        "role": "personal",
        "unlimited_credits": False
    }

#조직 가입 API 엔드포인트
@router.post(
    "/join",
    status_code=status.HTTP_201_CREATED
)
def apply_to_organization(body: JoinOrganizationRequest, request: Request, session: Session = Depends(get_db)):

    #사용자가 로그인 되어있는지 확인
    user_id = _require_user_id(request)

    #조합장은 다른 조합에 가입할 수 없다
    if get_led_organization(session, user_id):
        raise BadRequestException("조합장 계정은 다른 조합에 가입 신청할 수 없습니다.")

    #초대코드에 대한 조직 조회
    code = body.invitation_code.strip().upper()
    organization = session.execute(
        select(Organization).where(
            Organization.invitation_code == code
        )
    ).scalar_one_or_none()

    #조직이 존재하지 않거나 유효한 조직이 아니라면 Bad Request Exception 반환
    if not organization or not is_organization_active(organization):
        raise BadRequestException("유효한 조합 코드가 아니거나 플랜 기간이 종료되었습니다.")

    #이미 조합 신청 혹은 가입 상태인지 확인
    if get_membership(session, user_id):
        raise ConflictException("이미 조합 가입 신청 또는 가입 상태입니다.")

    #조직 멤버십 추가 (조합장 승인 대기)
    session.add(OrganizationMembership(organization_id=organization.organization_id, user_id=user_id, status="pending"))
    session.commit()

    return {"status": "pending", "message": "조합장 승인 대기 상태입니다."}

#사용자 조합 탈퇴 API 엔드포인트
@router.delete(
    "/leave",
    status_code=status.HTTP_204_NO_CONTENT
)
def leave_organization(request: Request, session: Session = Depends(get_db)):

    #사용자가 로그인 되어있는지 확인
    user_id = _require_user_id(request)

    membership = get_membership(session, user_id)
    if not membership:
        raise BadRequestException("탈퇴할 조합 가입 내역이 없습니다.")

    session.delete(membership)
    session.commit()

#조직 멤버 조회 API 엔드포인트
@router.get("/members")
def list_members(request: Request, session: Session = Depends(get_db)):

    #로그인된 사용자인지 확인
    user_id = _require_user_id(request)

    #사용자가 조합장인 조직 조회
    organization = get_led_organization(session, user_id)
    if not organization:
        raise BadRequestException("조합장 계정만 구성원을 관리할 수 있습니다.")

    #조직의 모든 가입자 명단 신청일 순으로 조회
    rows = session.execute(
        select(OrganizationMembership, User)
        .join(User, User.user_id == OrganizationMembership.user_id)
        .where(OrganizationMembership.organization_id == organization.organization_id)
        .order_by(OrganizationMembership.applied_at)
    ).all()

    return {
        "max_members": organization.max_members,
        "members": [
            {
                "user_id": user.user_id,
                "user_name": user.user_name,
                "email": user.email,
                "status": membership.status,
                "applied_at": membership.applied_at.isoformat(),
            }
            for membership, user in rows
        ],
    }

#멤버 조직 가입 승인 API 엔드포인트
@router.post("/members/{member_user_id}/approve")
def approve_member(member_user_id: int, request: Request, session: Session = Depends(get_db)):

    #사용자가 로그인 되어있는지 확인
    user_id = _require_user_id(request)

    #활성 플랜이 있는 조합장인지 확인
    organization = get_led_organization(session, user_id)
    if not organization or not is_organization_active(organization):
        raise BadRequestException("활성 플랜이 있는 조합장만 가입을 승인할 수 있습니다.")

    #승인 대기 중인 가입 신청 조회
    membership = session.execute(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization.organization_id,
            OrganizationMembership.user_id == member_user_id,
        )
    ).scalar_one_or_none()
    if not membership or membership.status != "pending":
        raise BadRequestException("승인 대기 중인 가입 신청을 찾을 수 없습니다.")

    #플랜 최대 인원 확인
    if count_active_members(session, organization.organization_id) >= organization.max_members:
        raise BadRequestException("플랜의 최대 인원에 도달했습니다.")

    membership.status = "active"
    membership.approved_at = datetime.now(timezone.utc)
    session.commit()
    return {"status": "active"}

#멤버 삭제 API 엔드포인트
@router.delete(
    "/members/{member_user_id}",
    status_code=status.HTTP_204_NO_CONTENT
)
def remove_member(member_user_id: int, request: Request, session: Session = Depends(get_db)):

    #사용자가 로그인되어있는지 확인
    user_id = _require_user_id(request)

    #사용자가 조합장인 조직 조회
    organization = get_led_organization(session, user_id)
    if not organization:
        raise BadRequestException("조합장 계정만 구성원을 관리할 수 있습니다.")

    #삭제하려는 멤버의 멤버십 정보 조회
    membership = session.execute(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization.organization_id,
            OrganizationMembership.user_id == member_user_id,
        )
    ).scalar_one_or_none()
    if not membership:
        raise BadRequestException("해당 구성원을 찾을 수 없습니다.")

    session.delete(membership)
    session.commit()
