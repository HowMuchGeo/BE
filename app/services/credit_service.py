from app.exceptions.exceptions_handler import BadRequestException, ServiceUnavailableException
from app.cache import keys
from app.cache.store import RedisUnavailableError, redis_store
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from datetime import datetime, time, timedelta
import logging
import secrets
from zoneinfo import ZoneInfo

#일일 사용 가능한 크레딧 수 제한
DAILY_CREDIT_LIMIT = 5

#타임존 설정 (서울 시간)
SEOUL_TIMEZONE = ZoneInfo("Asia/Seoul")

#백엔드 로거 설정
logger = logging.getLogger(__name__)

#Lua 를 통하여 Redis에서 키를 가져오고, 없으면 초기화하며, 만료 시간을 설정
_GET_OR_INITIALIZE_SCRIPT = """
local credits = redis.call('GET', KEYS[1])
if not credits then
    redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
    credits = ARGV[1]
end
return tonumber(credits)
"""

#크레딧이 차감될 계산을 위한 일회성 토큰을 자정까지 보관
_ISSUE_CREDIT_TOKEN_SCRIPT = """
return redis.call('SET', KEYS[1], 'pending', 'EX', ARGV[1], 'NX')
"""

#결제 주문당 한 번만 구매 크레딧을 충전
_FULFILL_PURCHASE_SCRIPT = """
if redis.call('SET', KEYS[2], 'fulfilled', 'NX') then
    return redis.call('INCRBY', KEYS[1], ARGV[1])
end
return tonumber(redis.call('GET', KEYS[1]) or '0')
"""

#계산 성공 후 토큰당 한 번만 크레딧을 차감. 같은 구역의 자동 재계산은 기존 차감을 재사용
#반환값 : 남은 크레딧 수 | -1 잔액 없음 | -2 토큰 무효 | -3 무제한 이용자
_CONSUME_CREDIT_TOKEN_SCRIPT = """
local token_state = redis.call('GET', KEYS[2])
local credits = redis.call('GET', KEYS[1])
local purchased = tonumber(redis.call('GET', KEYS[3]) or '0')
if token_state == 'unlimited-consumed' then
    if ARGV[3] == '1' then
        return -3
    end
    return -2
end
if ARGV[3] == '1' and token_state == 'pending' then
    redis.call('SET', KEYS[2], 'unlimited-consumed', 'EX', ARGV[2])
    return -3
end
if token_state == 'consumed' then
    if not credits then
        redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
        credits = ARGV[1]
    end
    return tonumber(credits) + purchased
end
if token_state ~= 'pending' then
    return -2
end
if not credits then
    redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
    credits = ARGV[1]
end
if tonumber(credits) > 0 then
    credits = redis.call('DECR', KEYS[1])
elseif purchased > 0 then
    purchased = redis.call('DECR', KEYS[3])
else
    return -1
end
redis.call('SET', KEYS[2], 'consumed', 'EX', ARGV[2])
return tonumber(credits) + tonumber(purchased)
"""

#Redis 장애를 사용자용 503 으로 변환
@asynccontextmanager
async def _redis_guard(message: str, log_context: str) -> AsyncIterator[None]:
    try:
        yield
    except RedisUnavailableError as err:
        logger.exception("[ Log ] : Redis 크레딧 처리 실패 : %s", log_context)
        raise ServiceUnavailableException(message=message) from err

#일일 크레딧 키와 TTL(다음 날 자정까지 남은 초), 초기화 시각
def _daily_key_and_ttl(user_id: int) -> tuple[str, int, datetime]:
    now = datetime.now(SEOUL_TIMEZONE)
    resets_at = datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=SEOUL_TIMEZONE)
    ttl_seconds = max(1, int((resets_at - now).total_seconds()))
    return keys.daily_credits(user_id, f"{now:%Y-%m-%d}"), ttl_seconds, resets_at

#크레딧을 차감할 수 있는 계산 토큰 발급
async def issue_credit_token(user_id: int) -> str:
    daily_key, ttl_seconds, _ = _daily_key_and_ttl(user_id)
    token = secrets.token_urlsafe(32)

    async with _redis_guard("크레딧 정보를 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.", f"issue token user={user_id}"):
        issued = await redis_store.run_script(
            _ISSUE_CREDIT_TOKEN_SCRIPT,
            keys=[keys.credit_token(daily_key, token)],
            args=[ttl_seconds],
        )

    if issued is None:
        raise ServiceUnavailableException(message="계산 토큰을 발급할 수 없습니다. 다시 시도해 주세요.")
    return token

#계산 성공 후 토큰을 소비. 동일 토큰의 재계산은 추가 차감하지 않음. 무제한 이용자는 -1 반환
async def consume_credit_token(user_id: int, token: str, unlimited: bool = False) -> int:
    daily_key, ttl_seconds, _ = _daily_key_and_ttl(user_id)

    async with _redis_guard("크레딧을 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.", f"consume token user={user_id}"):
        remaining = int(await redis_store.run_script(
            _CONSUME_CREDIT_TOKEN_SCRIPT,
            keys=[daily_key, keys.credit_token(daily_key, token), keys.purchased_credits(user_id)],
            args=[DAILY_CREDIT_LIMIT, ttl_seconds, 1 if unlimited else 0],
        ))

    if remaining == -3:
        return -1
    if remaining == -1:
        raise ServiceUnavailableException(message="오늘 사용할 수 있는 크레딧을 모두 사용했습니다.")
    if remaining == -2:
        raise BadRequestException("계산 토큰이 만료되었거나 유효하지 않습니다. 구역을 다시 분석해 주세요.")
    return remaining

#일일 크레딧 · 구매 크레딧 잔액 조회
async def get_daily_credits(user_id: int) -> dict[str, int | str]:
    daily_key, ttl_seconds, resets_at = _daily_key_and_ttl(user_id)

    async with _redis_guard("크레딧 정보를 불러올 수 없습니다. 잠시 후 다시 시도해 주세요.", f"load credits user={user_id}"):
        daily_remaining = int(await redis_store.run_script(
            _GET_OR_INITIALIZE_SCRIPT,
            keys=[daily_key],
            args=[DAILY_CREDIT_LIMIT, ttl_seconds],
        ))
        purchased_remaining = int(await redis_store.get(keys.purchased_credits(user_id)) or 0)

    return {
        "credits_remaining": daily_remaining + purchased_remaining,
        "daily_credits_remaining": daily_remaining,
        "purchased_credits": purchased_remaining,
        "daily_credit_limit": DAILY_CREDIT_LIMIT,
        "resets_at": resets_at.isoformat(),
    }

#결제 승인된 크레딧 충전. 같은 주문은 한 번만 반영되며, 충전 후 구매 크레딧 잔액을 반환
async def fulfill_credit_purchase(user_id: int, partner_order_id: str, credits: int) -> int:
    async with _redis_guard(
        "결제는 승인됐지만 크레딧 반영을 확인할 수 없습니다. 고객센터에 문의해 주세요.",
        f"fulfill purchase order={partner_order_id}",
    ):
        return int(await redis_store.run_script(
            _FULFILL_PURCHASE_SCRIPT,
            keys=[keys.purchased_credits(user_id), keys.credit_purchase_fulfilled(partner_order_id)],
            args=[credits],
        ))

#남은 크레딧(일일 + 구매)이 없으면 503
async def ensure_daily_credit_available(user_id: int) -> None:
    credits = await get_daily_credits(user_id)
    if credits["credits_remaining"] <= 0:
        raise ServiceUnavailableException(message="오늘 사용할 수 있는 크레딧을 모두 사용했습니다.")
