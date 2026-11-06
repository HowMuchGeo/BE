import os

import pytest
from dotenv import load_dotenv

#=== 환경변수 준비 (app 모듈 import 전에) ===#
#app.cache.redis · app.database.database_connection 은 import 시점에 URL 이 없으면 ValueError 를 던진다.
#테스트는 실제 DB · Redis 에 붙지 않으므로 값이 없을 때만 더미로 채운다 (.env 가 있으면 그 값이 우선)
load_dotenv()
os.environ.setdefault("POSTGRESQL_DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from tests.fakes import FakeRedis  # noqa: E402

#테스트용 외부 API 설정값. 실제 .env 값과 상관없이 테스트마다 고정한다
TEST_ENV = {
    "VWORLD_API_KEY": "test-vworld-key",
    "VWORLD_DOMAIN": "test.domain",
    "PROXY_URL": "https://proxy.test/",
    "MOLIT_API_KEY": "test-molit-key",
    "BLDRGST_API_KEY": "test-bldrgst-key",
    "KAKAO_API_KEY": "test-kakao-key",
    "KAKAO_PAYMENT_SECERT_KEY": "test-kakaopay-secret",
    "KAKAO_PAYMENT_CID": "TC0ONETIME",
    "KAKAO_PAYMENT_BASE_URL": "https://pay.test/online/v1/payment/",
    "BACKEND_URL": "http://backend.test/",
    "FRONTEND_URL": "http://frontend.test/",
}


#=== pytest --integration 옵션 ===#
def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--integration",
        action="store_true",
        default=False,
        help="실제 PostgreSQL / Redis 에 붙는 integration 테스트까지 실행",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--integration"):
        return
    skip_integration = pytest.mark.skip(reason="--integration 옵션을 주면 실행")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)


#=== 공통 Fixture ===#
@pytest.fixture(autouse=True)
def test_env(monkeypatch: pytest.MonkeyPatch) -> dict:
    """외부 API 설정값을 테스트 값으로 고정 (settings 는 접근 시점에 읽으므로 바로 반영된다)"""
    for key, value in TEST_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("BUILDING_LEDGER_API_KEY", raising=False)
    return dict(TEST_ENV)


@pytest.fixture(autouse=True)
def sleep_calls(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """time.sleep 을 막고 호출 기록만 남긴다 (호출 간격 · 재시도 대기 때문에 테스트가 느려지지 않게)"""
    import time

    calls: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda seconds: calls.append(seconds))
    return calls


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    """zone_service 의 Redis 를 메모리 Fake 로 교체"""
    import app.services.zone_service as zone_service

    redis = FakeRedis()
    monkeypatch.setattr(zone_service, "redis_client", redis)
    return redis


@pytest.fixture
def client():
    """health 라우터만 등록한 최소 앱.

    app.main 을 쓰지 않는 이유
    - startup 이벤트(create_tables)가 실제 DB 접속을 시도함
    - 다른 라우터들이 import 시점에 각자 환경변수를 요구함
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routers.health import router as health_router

    app = FastAPI()
    app.include_router(health_router)

    with TestClient(app) as test_client:
        yield test_client
