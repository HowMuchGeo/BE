#외부 API 조회 결과 캐시 (Redis 접근 · 직렬화 · 실패 처리는 app.cache.store 가 담당)
from dataclasses import asdict, is_dataclass
from typing import Any, Optional
import logging

from app.cache import keys
from app.cache.store import redis_store
from app.services.predict.sale_price import Trade

#백엔드 Logger
logger = logging.getLogger(__name__)

#=== 실거래 (Trade) ===#
async def get_cached_trades(lawd_cd: str, target_ym: str) -> Optional[list]:
    raw_list = await redis_store.get_cache(keys.TRADES, lawd_cd, target_ym)
    if raw_list is None:
        return None

    #Trade 필드가 바뀐 뒤 남아있는 예전 캐시는 미스로 처리
    try:
        return [Trade(**item) if isinstance(item, dict) else item for item in raw_list]
    except TypeError as err:
        logger.warning(f"[ Log ] : Redis 캐시 형식 불일치 : Trade : {lawd_cd}:{target_ym} : {err}")
        return None

async def set_cached_trades(lawd_cd: str, target_ym: str, trades: list) -> None:
    await redis_store.set_cache(keys.TRADES, [_to_dict(trade) for trade in trades], lawd_cd, target_ym)

#캐시 저장용 dict 변환 (Pydantic v2 / v1, dataclass, dict, 일반 객체 순)
def _to_dict(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)

#=== 토지 (Land) ===#
async def get_cached_land(pnu: str) -> Optional[dict]:
    return await redis_store.get_cache(keys.LAND, pnu)

async def set_cached_land(pnu: str, data: dict) -> None:
    await redis_store.set_cache(keys.LAND, data, pnu)

#=== 건축물대장 ===#
async def get_cached_building(pnu: str) -> Optional[dict]:
    return await redis_store.get_cache(keys.BUILDING, pnu)

async def set_cached_building(pnu: str, data: dict) -> None:
    await redis_store.set_cache(keys.BUILDING, data, pnu)

#=== 전유면적 합계 ===#
async def get_cached_exclusive_total(pnu: str) -> Optional[float]:
    value = await redis_store.get_cache(keys.EXCLUSIVE_TOTAL, pnu)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

#0 은 "다 못 받았다" 는 뜻이라 캐시하지 않음 (set_cache 가 빈 값을 건너뜀)
async def set_cached_exclusive_total(pnu: str, total: float) -> None:
    await redis_store.set_cache(keys.EXCLUSIVE_TOTAL, total, pnu)

#=== 토지소유정보 ===#
async def get_cached_possession(pnu: str) -> Optional[dict]:
    return await redis_store.get_cache(keys.POSSESSION, pnu)

async def set_cached_possession(pnu: str, data: dict) -> None:
    await redis_store.set_cache(keys.POSSESSION, data, pnu)

#=== 재건축 (공동주택 단지 · 공시가격) ===#
async def get_cached_apartment(pnu: str) -> Optional[dict]:
    return await redis_store.get_cache(keys.APARTMENT, pnu)

async def set_cached_apartment(pnu: str, data: dict) -> None:
    await redis_store.set_cache(keys.APARTMENT, data, pnu)

async def get_cached_house_price(pnu: str) -> Optional[dict]:
    return await redis_store.get_cache(keys.HOUSE_PRICE, pnu)

async def set_cached_house_price(pnu: str, data: dict) -> None:
    await redis_store.set_cache(keys.HOUSE_PRICE, data, pnu)

async def get_cached_apartment_prices(pnu: str) -> Optional[dict]:
    return await redis_store.get_cache(keys.APARTMENT_PRICES, pnu)

async def set_cached_apartment_prices(pnu: str, data: dict) -> None:
    await redis_store.set_cache(keys.APARTMENT_PRICES, data, pnu)
