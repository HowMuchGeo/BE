import pytest
from fastapi import status
from fastapi.testclient import TestClient

import app.routers.health as health_module

HEALTH_URL = "/api/v1/health"
READY_URL = "/api/v1/health/ready"


#engine.connect() 가 반환하는 커넥션 대역 
class FakeConnection:

    def __init__(self) -> None:
        self.executed: list[str] = []

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execute(self, statement) -> None:
        self.executed.append(str(statement))

#SQLAlchemy Engine 대역
class FakeEngine:

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.connect_calls = 0
        self.connection = FakeConnection()

    def connect(self) -> FakeConnection:
        self.connect_calls += 1
        if self.error:
            raise self.error
        return self.connection

#redis.asyncio 클라이언트 대역
class FakeRedis:

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.ping_calls = 0

    async def ping(self) -> bool:
        self.ping_calls += 1
        if self.error:
            raise self.error
        return True


#Fixture : 의존성 교체
@pytest.fixture
def patch_deps(monkeypatch: pytest.MonkeyPatch):
    """health 모듈의 engine / redis_client 를 Fake 로 교체하는 헬퍼를 반환"""

    def _patch(db_error: Exception | None = None, redis_error: Exception | None = None):
        fake_engine = FakeEngine(error=db_error)
        fake_redis = FakeRedis(error=redis_error)
        monkeypatch.setattr(health_module, "engine", fake_engine)
        monkeypatch.setattr(health_module, "redis_client", fake_redis)
        return fake_engine, fake_redis

    return _patch


#GET /api/v1/health 테스트
class TestHealth:
    def test_returns_200_and_ok(self, client: TestClient, patch_deps) -> None:
        patch_deps()

        response = client.get(HEALTH_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "ok"}

    #DB / Redis 가 모두 죽어 있어도 liveness 는 200 이어야 한다
    def test_does_not_touch_db_or_redis(self, client: TestClient, patch_deps) -> None:
        fake_engine, fake_redis = patch_deps(
            db_error=ConnectionRefusedError("db down"),
            redis_error=ConnectionRefusedError("redis down"),
        )

        response = client.get(HEALTH_URL)

        assert response.status_code == status.HTTP_200_OK
        assert fake_engine.connect_calls == 0
        assert fake_redis.ping_calls == 0


#GET /api/v1/health/ready 테스트
class TestReadiness:
    def test_all_ok_returns_200(self, client: TestClient, patch_deps) -> None:
        fake_engine, fake_redis = patch_deps()

        response = client.get(READY_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "status": "ok",
            "checks": {
                "fastapi": "ok",
                "postgresql": "ok",
                "redis": "ok",
            },
        }

        #실제로 DB 에 SELECT 1, Redis 에 PING 을 보냈는지
        assert fake_engine.connection.executed == ["SELECT 1"]
        assert fake_redis.ping_calls == 1

    @pytest.mark.parametrize(
        ("db_error", "redis_error", "expected_checks"),
        [
            pytest.param(
                ConnectionRefusedError("db down"),
                None,
                {"fastapi": "ok", "postgresql": "error: ConnectionRefusedError", "redis": "ok"},
                id="postgresql-down",
            ),
            pytest.param(
                None,
                TimeoutError("redis timeout"),
                {"fastapi": "ok", "postgresql": "ok", "redis": "error: TimeoutError"},
                id="redis-down",
            ),
            pytest.param(
                ConnectionRefusedError("db down"),
                TimeoutError("redis timeout"),
                {
                    "fastapi": "ok",
                    "postgresql": "error: ConnectionRefusedError",
                    "redis": "error: TimeoutError",
                },
                id="both-down",
            ),
        ],
    )
    def test_dependency_down_returns_503(
        self,
        client: TestClient,
        patch_deps,
        db_error: Exception | None,
        redis_error: Exception | None,
        expected_checks: dict[str, str],
    ) -> None:
        patch_deps(db_error=db_error, redis_error=redis_error)
        response = client.get(READY_URL)
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.json() == {"status": "degraded", "checks": expected_checks}

    #DB 가 실패해도 Redis 검사는 계속 수행되어야 한다
    def test_db_failure_does_not_skip_redis_check(self, client: TestClient, patch_deps) -> None:
        _, fake_redis = patch_deps(db_error=ConnectionRefusedError("db down"))
        client.get(READY_URL)
        assert fake_redis.ping_calls == 1

    #예외 메시지(접속 정보 등)는 응답에 노출되지 않고, 예외 타입명만 노출되어야 한다
    def test_error_detail_is_not_exposed(self, client: TestClient, patch_deps) -> None:
        secret = "postgresql://howmuch:super-secret@10.0.0.1:5432/howmuch"
        patch_deps(
            db_error=ConnectionRefusedError(secret),
            redis_error=ConnectionRefusedError(secret),
        )
        response = client.get(READY_URL)
        assert "super-secret" not in response.text
        assert "10.0.0.1" not in response.text


#Integration 테스트
@pytest.mark.integration
def test_ready_with_real_infra(client: TestClient) -> None:
    """사전 준비 : docker compose -f docker-compose.local.yml up -d
    실행      : pytest --integration
    """
    response = client.get(READY_URL)

    assert response.status_code == status.HTTP_200_OK, response.json()
    assert response.json()["status"] == "ok"