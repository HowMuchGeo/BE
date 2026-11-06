from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from app.exceptions.exceptions_handler import add_exception_handlers
from app.database.database_connection import engine
from dotenv import load_dotenv
from app.database.orm import Base
from app.config.settings import settings
import os
import httpx
import gdown
import traceback

#.env 파일 로드
load_dotenv()

#필수 환경변수 검증 — 하나라도 없으면 서버가 뜨지 않는다 (예전에는 각 라우터가 import 시점에 검사)
settings.validate_required()

#환경 변수 로드
FRONTEND_URL = os.getenv("FRONTEND_URL")
COOKIE_SECRET_KEY = os.getenv("COOKIE_SECRET_KEY")

#FastAPI 객체 생성
app = FastAPI()

#FastAPI 라우터 import
from app.routers.cadastral import router as cadastral_router
from app.routers.zone import router as zone_router
from app.routers.contribution import router as contribution_router
from app.routers.user import router as user_router
from app.routers.news import router as news_router
from app.routers.payment import router as payment_router
from app.routers.organization import router as organization_router
from app.routers.health import router as health_router

#Database 테이블 생성 이벤트 핸들러 등록
@app.on_event("startup")
def create_tables():
    if os.getenv("RESET_DB", "").lower() == "true":
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

#AI 모델 라우터 등록
app.include_router(cadastral_router)
app.include_router(zone_router)
app.include_router(contribution_router)
app.include_router(user_router)
app.include_router(news_router)
app.include_router(payment_router)
app.include_router(organization_router)
app.include_router(health_router)

#사용자 정의 예외 처리기 등록
add_exception_handlers(app)

#Cookie 세션 관리 MiddleWare 설정
app.add_middleware(
    SessionMiddleware,
    secret_key=COOKIE_SECRET_KEY,
    same_site="none",
    https_only=True
)

#MiddleWare 설정 (개발단계 임시 설정)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL.rstrip("/")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)