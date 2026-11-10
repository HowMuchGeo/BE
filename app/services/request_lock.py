import logging
import secrets
from collections.abc import AsyncIterator, Callable

from fastapi import Request

from app.cache import keys
from app.cache.store import RedisUnavailableError, redis_store
from app.exceptions.exceptions_handler import TooManyRequestsException, UnauthorizedException

#백엔드 Logger
logger = logging.getLogger(__name__)

#잠금 만료(초). 정상 종료 시 finally 에서 바로 풀리고, 프로세스가 죽었을 때만 이 시간 뒤에 풀린다
DEFAULT_LOCK_TTL = 180

#내가 건 잠금일 때만 해제 (만료 후 다른 요청이 다시 건 잠금을 지우지 않도록)
_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


#잠금 획득. 이미 처리 중인 요청이 있으면 429.
#Redis 장애 시에는 잠금 없이 통과시키고 None 을 돌려준다 (잠금 때문에 서비스 전체가 멈추지 않도록)
async def acquire_user_lock(name: str, user_id: int, ttl: int = DEFAULT_LOCK_TTL) -> str | None:
    token = secrets.token_hex(16)
    try:
        acquired = await redis_store.set(keys.user_lock(name, user_id), token, ttl=ttl, nx=True)
    except RedisUnavailableError as err:
        logger.warning(f"[ Log ] : 요청 잠금 획득 실패, 잠금 없이 진행 : {name}:{user_id} : {err.__cause__ or err}")
        return None

    if not acquired:
        raise TooManyRequestsException("이전 분석 요청이 아직 처리 중입니다. 완료된 뒤 다시 시도해 주세요.")
    return token


async def release_user_lock(name: str, user_id: int, token: str | None) -> None:
    if token is None:
        return
    try:
        await redis_store.run_script(_RELEASE_SCRIPT, keys=[keys.user_lock(name, user_id)], args=[token])
    except RedisUnavailableError as err:
        logger.warning(f"[ Log ] : 요청 잠금 해제 실패, TTL 만료로 풀림 : {name}:{user_id} : {err.__cause__ or err}")


#사용자별 동시 실행 1건 제한 의존성.
#사용 : @router.post(..., dependencies=[Depends(per_user_lock("zone"))])
def per_user_lock(name: str, ttl: int = DEFAULT_LOCK_TTL) -> Callable[[Request], AsyncIterator[None]]:
    async def dependency(request: Request) -> AsyncIterator[None]:
        user_id = request.session.get("user_id")
        if user_id is None:
            raise UnauthorizedException("로그인이 필요합니다.")

        token = await acquire_user_lock(name, int(user_id), ttl)
        try:
            yield
        finally:
            await release_user_lock(name, int(user_id), token)

    return dependency
