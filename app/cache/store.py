#Redis 접근 단일 창구
#서비스 코드는 redis_client 를 직접 쓰지 않고 redis_store 를 통해서만 Redis 에 접근한다
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.cache.keys import CacheSpec
from app.cache.redis import redis_client

#백엔드 Logger
logger = logging.getLogger(__name__)

T = TypeVar("T")

#Redis 연결·명령 실패. 원인 예외는 __cause__ 에 남는다
class RedisUnavailableError(Exception):
    pass

#Redis 저장소
#- 캐시 (get_cache / set_cache) : 실패해도 서비스는 원본 조회로 계속 진행해야 하므로 로그만 남기고 None / False 반환
#- 상태 저장 (get / set / run_script / ping) : 크레딧 · 잠금처럼 정합성이 중요한 값. 실패 시 RedisUnavailableError
class RedisStore:

    def __init__(self, client: Redis) -> None:
        self._client = client

    #=== 캐시 ===#
    #캐시 조회. 없거나, 깨졌거나, Redis 가 실패하면 None
    async def get_cache(self, spec: CacheSpec, *parts: object) -> Any | None:
        key = spec.key(*parts)
        try:
            raw = await self._client.get(key)
        except (RedisError, OSError) as err:
            logger.warning(f"[ Log ] : Redis 캐시 불러오기 실패 : {spec.label} : {key} : {err}")
            return None

        if not raw:
            logger.warning(f"[ Log ] : Redis 캐시 존재하지 않음 : {spec.label} : {key}")
            return None

        try:
            value = json.loads(raw)
        except ValueError as err:
            logger.warning(f"[ Log ] : Redis 캐시 디코딩 실패 : {spec.label} : {key} : {err}")
            return None

        logger.warning(f"[ Log ] : Redis 캐시 불러오기 성공 : {spec.label} : {key}")
        return value

    #캐시 저장. 빈 값(None, {}, [], 0, "")은 "조회 실패"와 구분할 수 없어 저장하지 않는다. 저장했으면 True
    async def set_cache(self, spec: CacheSpec, value: Any, *parts: object) -> bool:
        if not value:
            return False

        key = spec.key(*parts)
        try:
            await self._client.set(key, json.dumps(value, ensure_ascii=False, default=str), ex=spec.ttl)
        except (RedisError, OSError, TypeError, ValueError) as err:
            logger.warning(f"[ Log ] : Redis 캐시 저장 실패 : {spec.label} : {key} : {err}")
            return False

        logger.warning(f"[ Log ] : Redis 캐시 저장 성공 : {spec.label} : {key} : (TTL {spec.ttl}s)")
        return True

    #=== 상태 저장 ===#
    async def get(self, key: str) -> str | None:
        return await self._strict(f"GET {key}", lambda: self._client.get(key))

    #값 저장. nx=True 이면 키가 없을 때만 저장하고, 저장했는지 여부를 반환
    async def set(self, key: str, value: str | int, *, ttl: int | None = None, nx: bool = False) -> bool:
        result = await self._strict(f"SET {key}", lambda: self._client.set(key, value, ex=ttl, nx=nx))
        return bool(result)

    #Lua 스크립트 실행 (여러 명령을 원자적으로 처리할 때)
    async def run_script(self, script: str, *, keys: Sequence[str], args: Sequence[object] = ()) -> Any:
        return await self._strict(
            f"EVAL {keys[0] if keys else ''}",
            lambda: self._client.eval(script, len(keys), *keys, *args),
        )

    #연결 확인. 어떤 실패든 RedisUnavailableError 로 감싼다 (원인 타입은 __cause__)
    async def ping(self) -> None:
        try:
            await self._client.ping()
        except Exception as err:
            raise RedisUnavailableError("PING") from err

    async def _strict(self, action: str, call: Callable[[], Awaitable[T]]) -> T:
        try:
            return await call()
        except (RedisError, OSError) as err:
            raise RedisUnavailableError(action) from err

#애플리케이션 전역 저장소 (테스트에서는 redis_store._client 를 Fake 로 교체한다)
redis_store = RedisStore(redis_client)
