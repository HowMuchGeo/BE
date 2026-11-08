import asyncio
import json
from datetime import date

import pytest
from redis.exceptions import RedisError

from app.cache.keys import CacheSpec
from app.cache.store import RedisStore, RedisUnavailableError
from tests.fakes import BrokenRedis, FakeRedis

SPEC = CacheSpec("test", 60, "Test")


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def store(redis: FakeRedis) -> RedisStore:
    return RedisStore(redis)


@pytest.fixture
def broken_store() -> RedisStore:
    return RedisStore(BrokenRedis())


#=== 캐시 (실패해도 예외 없음) ===#
class TestCache:
    def test_set_then_get_roundtrip(self, store: RedisStore, redis: FakeRedis) -> None:
        value = {"name": "가락동", "area": 12.5, "items": [1, 2]}

        assert asyncio.run(store.set_cache(SPEC, value, "11710", 1)) is True
        assert redis.store["test:11710:1"] == json.dumps(value, ensure_ascii=False)
        assert redis.ttl["test:11710:1"] == 60
        assert asyncio.run(store.get_cache(SPEC, "11710", 1)) == value

    def test_non_json_values_are_stringified(self, store: RedisStore) -> None:
        asyncio.run(store.set_cache(SPEC, {"day": date(2026, 11, 8)}, "k"))

        assert asyncio.run(store.get_cache(SPEC, "k")) == {"day": "2026-11-08"}

    @pytest.mark.parametrize("empty", [None, {}, [], 0, 0.0, ""])
    def test_empty_values_are_not_cached(self, store: RedisStore, redis: FakeRedis, empty) -> None:
        assert asyncio.run(store.set_cache(SPEC, empty, "k")) is False
        assert redis.store == {}

    def test_miss_returns_none(self, store: RedisStore) -> None:
        assert asyncio.run(store.get_cache(SPEC, "missing")) is None

    def test_corrupted_value_returns_none(self, store: RedisStore, redis: FakeRedis) -> None:
        redis.store["test:k"] = "{not-json"

        assert asyncio.run(store.get_cache(SPEC, "k")) is None

    def test_redis_failure_is_swallowed(self, broken_store: RedisStore) -> None:
        assert asyncio.run(broken_store.get_cache(SPEC, "k")) is None
        assert asyncio.run(broken_store.set_cache(SPEC, {"a": 1}, "k")) is False

    def test_failure_is_logged(self, broken_store: RedisStore, caplog: pytest.LogCaptureFixture) -> None:
        asyncio.run(broken_store.get_cache(SPEC, "k"))

        assert "Redis 캐시 불러오기 실패 : Test : test:k" in caplog.text


#=== 상태 저장 (실패 시 RedisUnavailableError) ===#
class TestStrictOperations:
    def test_get_and_set(self, store: RedisStore, redis: FakeRedis) -> None:
        assert asyncio.run(store.set("counter", 3, ttl=10)) is True
        assert redis.ttl["counter"] == 10
        assert asyncio.run(store.get("counter")) == "3"
        assert asyncio.run(store.get("missing")) is None

    def test_set_nx_only_once(self, store: RedisStore, redis: FakeRedis) -> None:
        assert asyncio.run(store.set("lock", "a", nx=True)) is True
        assert asyncio.run(store.set("lock", "b", nx=True)) is False
        assert redis.store["lock"] == "a"

    def test_run_script_passes_keys_then_args(self, store: RedisStore, redis: FakeRedis) -> None:
        redis.store["lock"] = "token"

        result = asyncio.run(store.run_script("SCRIPT", keys=["lock"], args=["token"]))

        assert result == 1
        assert redis.eval_calls == [("SCRIPT", 1, ("lock", "token"))]

    def test_ping(self, store: RedisStore, redis: FakeRedis) -> None:
        asyncio.run(store.ping())

        assert redis.ping_calls == 1

    @pytest.mark.parametrize(
        "operation",
        [
            pytest.param(lambda s: s.get("k"), id="get"),
            pytest.param(lambda s: s.set("k", "v"), id="set"),
            pytest.param(lambda s: s.run_script("SCRIPT", keys=["k"]), id="run_script"),
            pytest.param(lambda s: s.ping(), id="ping"),
        ],
    )
    def test_failures_raise_unavailable_with_cause(self, broken_store: RedisStore, operation) -> None:
        with pytest.raises(RedisUnavailableError) as exc:
            asyncio.run(operation(broken_store))

        assert isinstance(exc.value.__cause__, RedisError)

    def test_ping_wraps_any_error(self) -> None:
        store = RedisStore(BrokenRedis(error=TimeoutError("slow")))

        with pytest.raises(RedisUnavailableError) as exc:
            asyncio.run(store.ping())

        assert type(exc.value.__cause__) is TimeoutError
