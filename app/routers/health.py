from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool
from app.database.database_connection import engine
from app.cache.redis import redis_client

#서버 상태 조회 라우터
router = APIRouter(
    prefix="/api/v1",
    tags=["Health Router"]
)

#서버 상태 API 엔드포인트
@router.get(
    "/health"
)
async def get_health():
    return {
        "status": "ok"
    }

#서버 상태 총 확인 API 엔드포인트
@router.get(
    "/health/ready"
)
async def get_readiness():
    checks: dict[str, str] = {}

    #FastAPI 상태 확인
    checks["fastapi"] = "ok"

    #PostgreSQL 상태 확인
    def _ping_db():
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    try:
        await run_in_threadpool(_ping_db)
        checks["postgresql"] = "ok"
    except Exception as e:
        checks["postgresql"] = f"error: {type(e).__name__}"

    #Redis 상태 확인
    try:
        await redis_client.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {type(e).__name__}"

    #상태 총 검사
    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "status": "ok" if healthy else "degraded",
            "checks": checks}
    )