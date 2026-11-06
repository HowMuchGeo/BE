import os
import pytest
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.testclient import TestClient

#환경변수 로드
load_dotenv()
os.getenv("POSTGRESQL_DATABASE_URL")
os.getenv("REDIS_URL")

#pytest --integration 옵션
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


#공통 Fixture
@pytest.fixture
def client() -> TestClient:
    """health 라우터만 등록한 최소 앱.

    app.main 을 쓰지 않는 이유
    - startup 이벤트(create_tables)가 실제 DB 접속을 시도함
    - 다른 라우터들이 import 시점에 각자 환경변수를 요구함
    """
    from app.routers.health import router as health_router

    app = FastAPI()
    app.include_router(health_router)

    with TestClient(app) as test_client:
        yield test_client