import asyncio
import os
import secrets
from collections.abc import Awaitable, Callable

import pytest
import redis.asyncio as aioredis

import app.services.credit_service as credit_service
from app.cache.store import redis_store
from app.exceptions.exceptions_handler import BadRequestException, ServiceUnavailableException
from tests.fakes import BrokenRedis

LIMIT = credit_service.DAILY_CREDIT_LIMIT


#=== Unit : Redis 장애 시 503 으로 변환 ===#
@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda: credit_service.issue_credit_token(1), id="issue"),
        pytest.param(lambda: credit_service.consume_credit_token(1, "token"), id="consume"),
        pytest.param(lambda: credit_service.get_daily_credits(1), id="get"),
        pytest.param(lambda: credit_service.fulfill_credit_purchase(1, "order", 5), id="fulfill"),
        pytest.param(lambda: credit_service.ensure_daily_credit_available(1), id="ensure"),
    ],
)
def test_redis_failure_becomes_503(monkeypatch: pytest.MonkeyPatch, call) -> None:
    monkeypatch.setattr(redis_store, "_client", BrokenRedis())

    with pytest.raises(ServiceUnavailableException) as exc:
        asyncio.run(call())

    assert exc.value.status_code == 503


def test_daily_key_expires_at_next_midnight_in_seoul() -> None:
    key, ttl, resets_at = credit_service._daily_key_and_ttl(42)

    assert key.startswith("credits:42:")
    assert (resets_at.hour, resets_at.minute, resets_at.second) == (0, 0, 0)
    assert 0 < ttl <= 86_400


#=== Integration : 실제 Redis 에서 Lua 스크립트 동작 검증 ===#
#사전 준비 : docker compose -f docker-compose.local.yml up -d
#실행      : pytest --integration
Scenario = Callable[[int], Awaitable[None]]


@pytest.fixture
def run_with_real_redis(monkeypatch: pytest.MonkeyPatch) -> Callable[[Scenario], None]:
    """테스트마다 겹치지 않는 user_id 로 시나리오를 실행하고, 그 user_id 의 키만 지운다"""

    def run(scenario: Scenario) -> None:
        async def main() -> None:
            client = aioredis.from_url(os.environ["REDIS_URL"], encoding="utf-8", decode_responses=True)
            monkeypatch.setattr(redis_store, "_client", client)
            user_id = 10**12 + secrets.randbelow(10**9)
            try:
                await scenario(user_id)
            finally:
                async for key in client.scan_iter(match=f"*{user_id}*"):
                    await client.delete(key)
                await client.aclose()

        asyncio.run(main())

    return run


async def _calculate(user_id: int, unlimited: bool = False) -> int:
    token = await credit_service.issue_credit_token(user_id)
    return await credit_service.consume_credit_token(user_id, token, unlimited=unlimited)


@pytest.mark.integration
class TestCreditsWithRealRedis:
    def test_new_user_starts_with_daily_limit(self, run_with_real_redis) -> None:
        async def scenario(user_id: int) -> None:
            credits = await credit_service.get_daily_credits(user_id)

            assert credits["credits_remaining"] == LIMIT
            assert credits["daily_credits_remaining"] == LIMIT
            assert credits["purchased_credits"] == 0

        run_with_real_redis(scenario)

    def test_same_token_is_charged_only_once(self, run_with_real_redis) -> None:
        async def scenario(user_id: int) -> None:
            token = await credit_service.issue_credit_token(user_id)

            assert await credit_service.consume_credit_token(user_id, token) == LIMIT - 1
            #같은 구역 재계산
            assert await credit_service.consume_credit_token(user_id, token) == LIMIT - 1
            assert (await credit_service.get_daily_credits(user_id))["credits_remaining"] == LIMIT - 1

        run_with_real_redis(scenario)

    def test_unknown_token_is_rejected(self, run_with_real_redis) -> None:
        async def scenario(user_id: int) -> None:
            with pytest.raises(BadRequestException):
                await credit_service.consume_credit_token(user_id, "not-issued")

        run_with_real_redis(scenario)

    def test_exhausted_daily_credits_raise_503(self, run_with_real_redis) -> None:
        async def scenario(user_id: int) -> None:
            for expected in range(LIMIT - 1, -1, -1):
                assert await _calculate(user_id) == expected

            with pytest.raises(ServiceUnavailableException):
                await _calculate(user_id)
            with pytest.raises(ServiceUnavailableException):
                await credit_service.ensure_daily_credit_available(user_id)

        run_with_real_redis(scenario)

    def test_purchased_credits_are_used_after_daily_credits(self, run_with_real_redis) -> None:
        async def scenario(user_id: int) -> None:
            assert await credit_service.fulfill_credit_purchase(user_id, f"order-{user_id}", 5) == 5
            for _ in range(LIMIT):
                await _calculate(user_id)

            credits = await credit_service.get_daily_credits(user_id)
            assert (credits["daily_credits_remaining"], credits["purchased_credits"]) == (0, 5)

            assert await _calculate(user_id) == 4
            credits = await credit_service.get_daily_credits(user_id)
            assert (credits["daily_credits_remaining"], credits["purchased_credits"]) == (0, 4)

        run_with_real_redis(scenario)

    def test_purchase_is_fulfilled_once_per_order(self, run_with_real_redis) -> None:
        async def scenario(user_id: int) -> None:
            order_id = f"order-{user_id}"

            assert await credit_service.fulfill_credit_purchase(user_id, order_id, 5) == 5
            assert await credit_service.fulfill_credit_purchase(user_id, order_id, 5) == 5
            assert await credit_service.fulfill_credit_purchase(user_id, f"{order_id}-2", 5) == 10

        run_with_real_redis(scenario)

    def test_unlimited_user_is_not_charged(self, run_with_real_redis) -> None:
        async def scenario(user_id: int) -> None:
            token = await credit_service.issue_credit_token(user_id)

            assert await credit_service.consume_credit_token(user_id, token, unlimited=True) == -1
            assert await credit_service.consume_credit_token(user_id, token, unlimited=True) == -1
            assert (await credit_service.get_daily_credits(user_id))["credits_remaining"] == LIMIT

            #무제한으로 쓴 토큰은 플랜 만료 후 일반 차감에 재사용할 수 없다
            with pytest.raises(BadRequestException):
                await credit_service.consume_credit_token(user_id, token)

        run_with_real_redis(scenario)
