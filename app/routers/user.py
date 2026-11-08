from fastapi import APIRouter, status, Depends, Request
from sqlalchemy.orm import Session
from sqlalchemy import select, or_
from app.database.database_connection import get_db
from app.models.user import User
from app.auth.encrypt import password_manager
from app.schemas.user.user_request import UserSignUpRequest, UserLoginRequest
from app.schemas.user.user_response import UserSignUpResponse
from app.exceptions.exceptions_handler import BadRequestException, ConflictException, UnauthorizedException
from app.services.credit_service import get_daily_credits
from app.services.organization_service import get_active_organization, get_role

#User 라우터
router = APIRouter(
    prefix="/api/v1",
    tags=["User Router"]
)

#회원가입 API 엔드포인트
#모든 계정은 개인 계정으로 생성된다. 조합장은 가입 후 조합 플랜을 결제하면 된다
@router.post(
    "/user/signup",
    status_code = status.HTTP_201_CREATED,
    response_model = UserSignUpResponse
)
def signup_user_handler(body: UserSignUpRequest, session: Session = Depends(get_db)):

    #같은 아이디 혹은 이메일이 있는지 확인
    users = session.execute(
        select(User).where(
            or_(
                User.user_name == body.user_name,
                User.email == body.email
            )
        )
    ).scalars().all()

    #같은 아이디나 이메일이 존재한다면 ConflictException 발생
    for u in users:
        if u.user_name == body.user_name:
            raise ConflictException("이미 존재하는 아이디입니다.")
        if u.email == body.email:
            raise ConflictException("이미 가입된 이메일입니다.")

    #비밀번호 암호화 후 저장
    user = User(
        user_name = body.user_name,
        email = body.email,
        password = password_manager.hash_password(body.password),
    )
    session.add(user)
    session.commit()

    return {
        "user_id": user.user_id,
        "user_name": user.user_name,
        "email": user.email,
    }

#로그인 API 엔드포인트
@router.post(
    "/user/login",
    status_code = status.HTTP_200_OK
)
def login_user_handler(request:Request, body: UserLoginRequest, session: Session = Depends(get_db)):

    #사용자 이름으로 사용자 조회
    user = session.execute(
        select(User).where(
            or_(
                User.user_name == body.user_name,
                User.email == body.user_name,
            )
        )
    ).scalar_one_or_none()

    #사용자가 존재하지 않으면 UnauthorizedException 발생
    if not user:
        raise UnauthorizedException("틀린 아이디 혹은 비밀번호입니다.")

    #비밀번호 검증 및 필요시 해시 업데이트
    is_valid, new_hash = password_manager.verify_and_update_password(
        plain_password=body.password,
        hashed_password=user.password
    )

    #비밀번호가 일치하지 않으면 UnauthorizedException 발생
    if not is_valid:
        raise  UnauthorizedException("이메일 또는 비밀번호가 일치하지 않습니다.")

    #비밀번호 해시가 업데이트되었다면 데이터베이스에 반영
    if new_hash:
        user.password = new_hash
        session.commit()

    #로그인 성공 시 세션에 사용자 ID 저장
    request.session.clear()
    request.session["user_id"] = user.user_id
    return {"message": "로그인에 성공했습니다."}

#로그아웃 API 엔드포인트
@router.post(
    "/user/logout",
    status_code = status.HTTP_200_OK
)
def logout_user_handler(request: Request):

    #로그인 상태 확인
    if "user_id" not in request.session:
        raise UnauthorizedException("로그인 상태가 아닙니다.")

    #세션에서 사용자 ID 제거
    request.session.clear()
    return {"message":"로그아웃에 성공했습니다."}

#사용자 정보 조회 API 엔드포인트
@router.get(
    "/user/info",
    status_code = status.HTTP_200_OK
)
def get_user_info_handler(request: Request, session: Session = Depends(get_db)):

    #로그인 상태 확인
    if "user_id" not in request.session:
        raise UnauthorizedException("로그인 상태가 아닙니다.")

    user_id = request.session["user_id"]

    #사용자 정보 조회
    user = session.execute(
        select(User).where(
            User.user_id == user_id
        )
    ).scalar_one_or_none()

    #사용자가 존재하지 않으면 UnauthorizedException 발생
    if not user:
        raise UnauthorizedException("사용자를 찾을 수 없습니다.")

    return {
        "user_id": user.user_id,
        "user_name": user.user_name,
        "email": user.email,
        #personal | leader | member
        "role": get_role(session, user.user_id),
    }

#사용자 크레딧 조회 API 엔드포인트
@router.get(
    "/user/credits",
    status_code=status.HTTP_200_OK,
)
async def get_user_credits_handler(request: Request, session: Session = Depends(get_db)):

    #로그인된 사용자 ID 조회
    user_id = request.session.get("user_id")
    if user_id is None:
        raise UnauthorizedException("로그인이 필요합니다.")
    user_id = int(user_id)

    #사용자가 활성 조합에 소속되어 무제한 크레딧이 제공된다면 무한 제공값으로 반환
    organization = get_active_organization(session, user_id)
    if organization:
        return {
            "credits_remaining": -1,
            "daily_credits_remaining": -1,
            "purchased_credits": 0,
            "daily_credit_limit": -1,
            "resets_at": organization.paid_until.isoformat(),
            "unlimited": True,
        }

    #개인 사용자라면 잔여 기본 크레딧 반환
    result = await get_daily_credits(user_id)
    return {**result, "unlimited": False}

#사용자 크레딧 초기화 API 엔드포인트
@router.post(
    "/user/credits/reset",
    status_code=status.HTTP_200_OK,
)
async def reset_user_credits_handler(request: Request, session: Session = Depends(get_db)):

    #로그인된 사용자 ID 확인
    user_id = request.session.get("user_id")
    if user_id is None:
        raise UnauthorizedException("로그인이 필요합니다.")

    #사용자가 조합 소속이라 무제한 크레딧이 가능한 상황이라면 크레딧 충전 실패 반환
    if get_active_organization(session, int(user_id)):
        raise BadRequestException("활성 조합 플랜 이용자는 크레딧 충전이 필요하지 않습니다.")

    raise BadRequestException("크레딧은 결제 승인 후 충전됩니다. 크레딧 구매 화면을 이용해 주세요.")
