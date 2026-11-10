import asyncio

import pytest
import app.services.request_lock as request_lock
from app.exceptions.exceptions_handler import TooManyRequestsException, UnauthorizedException
from tests.fakes import BrokenRedis, FakeRedis


class _FakeRequest:
    def __init__(self, user_id=None) -> None:
        self.session = {"user_id": user_id} if user_id is not None else {}


@pytest.fixture
def lock_redis(fake_redis: FakeRedis) -> FakeRedis:
    return fake_redis


def test_acquire_and_release(lock_redis: FakeRedis):
    async def scenario():
        token = await request_lock.acquire_user_lock("zone", 1, ttl=30)
        assert lock_redis.store["lock:zone:1"] == token
        assert lock_redis.ttl["lock:zone:1"] == 30

        await request_lock.release_user_lock("zone", 1, token)
        assert "lock:zone:1" not in lock_redis.store

    asyncio.run(scenario())


def test_second_request_of_same_user_is_rejected(lock_redis: FakeRedis):
    async def scenario():
        await request_lock.acquire_user_lock("zone", 1)
        with pytest.raises(TooManyRequestsException) as exc:
            await request_lock.acquire_user_lock("zone", 1)
        assert exc.value.status_code == 429

    asyncio.run(scenario())


def test_other_users_and_other_names_are_independent(lock_redis: FakeRedis):
    async def scenario():
        assert await request_lock.acquire_user_lock("zone", 1)
        assert await request_lock.acquire_user_lock("zone", 2)
        assert await request_lock.acquire_user_lock("zone_type", 1)

    asyncio.run(scenario())


def test_release_does_not_delete_someone_elses_lock(lock_redis: FakeRedis):
    async def scenario():
        await request_lock.acquire_user_lock("zone", 1)
        current = lock_redis.store["lock:zone:1"]
        await request_lock.release_user_lock("zone", 1, "stale-token")
        assert lock_redis.store["lock:zone:1"] == current

    asyncio.run(scenario())


def test_redis_failure_lets_request_through(broken_redis: BrokenRedis):
    async def scenario():
        token = await request_lock.acquire_user_lock("zone", 1)
        assert token is None
        await request_lock.release_user_lock("zone", 1, token)
        assert broken_redis.calls == 1

    asyncio.run(scenario())


def test_dependency_releases_even_when_endpoint_fails(lock_redis: FakeRedis):
    dependency = request_lock.per_user_lock("zone")

    async def scenario():
        gen = dependency(_FakeRequest(user_id=7))
        await gen.__anext__()
        assert "lock:zone:7" in lock_redis.store

        #엔드포인트에서 예외가 난 상황
        with pytest.raises(RuntimeError):
            await gen.athrow(RuntimeError("boom"))
        assert "lock:zone:7" not in lock_redis.store

    asyncio.run(scenario())


def test_dependency_requires_login(lock_redis: FakeRedis):
    dependency = request_lock.per_user_lock("zone")

    async def scenario():
        with pytest.raises(UnauthorizedException):
            await dependency(_FakeRequest()).__anext__()

    asyncio.run(scenario())
