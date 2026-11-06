#필지(토지·주택·건물) 정보 서비스
import asyncio
import logging
import statistics
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime

from app.clients import vworld
from app.services.building_ledger_service import fetch_apartment_complex, fetch_building, fetch_exclusive_total
from app.services.engine.prior_asset import is_public_owner
from app.services.zone_service import (
    get_cached_apartment, get_cached_apartment_prices, get_cached_building, get_cached_exclusive_total,
    get_cached_house_price, get_cached_land, get_cached_possession, set_cached_apartment, set_cached_apartment_prices,
    set_cached_building, set_cached_exclusive_total, set_cached_house_price, set_cached_land, set_cached_possession,
)

#백엔드 Logger
logger = logging.getLogger(__name__)

#V-World 동시 조회 수
POSSESSION_WORKERS = 4
vworld_executor = ThreadPoolExecutor(max_workers=POSSESSION_WORKERS)

#건축HUB 는 동시 호출에 민감
building_hub_executor = ThreadPoolExecutor(max_workers=1)

async def _run(executor: ThreadPoolExecutor, func, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, func, *args)

#연도별 이력 중 최신 기준연도 행
def _latest(rows: list[dict]) -> dict:
    return max(rows, key=lambda row: str(row.get("stdrYear", "")))

#=== 개별공시지가 ===#
#제곱미터당 땅 가격. 실패하면 0
async def fetch_land_price_per_m2(pnu: str) -> int:
    try:
        logger.warning(f"[ Log ] : PNU : {pnu} 에 대한 가격을 조회중입니다.")
        rows = await _run(vworld_executor, vworld.get_indvd_land_prices, pnu)

        #연도별 이력이 오므로 가장 최근 기준연도를 사용
        price = _latest(rows).get("pblntfPclnd") if rows else None
        logger.warning(f"[ Log ] : PNU : {pnu} 의 가격은 {price} 입니다.")

        return int(price) if price else 0

    except Exception as err:
        logger.warning(f"[ Log ] : get_land_price_per_m2 API 호출에서 오류가 발생했습니다 : {str(err)}")
        return 0

#=== 토지특성 ===#
LAND_DEFAULT = {
    "area_m2": 300.0,
    "zoning": "제2종일반주거지역",
    "land_category": "대",
    "land_price_per_m2": 0,
    "fallback": True,
}

#토지별 특성 정보 (캐시 경유). 실패하거나 행이 없으면 기본값
async def fetch_land_characteristics(pnu: str) -> dict:
    cached = await get_cached_land(pnu)
    if cached:
        return cached

    try:
        rows = await _run(vworld_executor, vworld.get_land_characteristics, pnu)
        if not rows:
            return dict(LAND_DEFAULT)

        land_info = _latest(rows)

        #데이터에서 값 추출. 없다면 기본값 사용
        result = {
            "area_m2": float(land_info.get("lndpclAr") or LAND_DEFAULT["area_m2"]),
            "zoning": land_info.get("prposArea1Nm") or land_info.get("prposArea1Cd") or LAND_DEFAULT["zoning"],
            "land_category": land_info.get("lndcgrCodeNm") or land_info.get("lndcgrCode") or LAND_DEFAULT["land_category"],
            "land_price_per_m2": int(float(land_info.get("pblntfPclnd") or LAND_DEFAULT["land_price_per_m2"])),
            "fallback": False,
        }

        await set_cached_land(pnu, result)
        return result

    except Exception as err:
        logger.warning(f"[ Log ] : V-World Land Characteristics API 오류 {pnu}: {err}")
        return dict(LAND_DEFAULT)


#=== 토지소유정보 ===#
POSSESSION_SAMPLE_ROWS = 20
POSSESSION_EMPTY = {"owner_type": "", "owner_code": "", "fallback": True}

def _summarize_possessions(rows: list[dict], total: int | None) -> dict:
    if not rows:
        return dict(POSSESSION_EMPTY)

    owner_count = int(total or len(rows))
    owners = [((r.get("posesnSeCodeNm") or "").strip(), (r.get("posesnSeCode") or "").strip()) for r in rows]

    #국공유로 보는 건 표본이 모두 국공유일 때 뿐
    private = [o for o in owners if not is_public_owner(o[0])]
    pool = private or owners
    owner_type, owner_code = max(set(pool), key=pool.count)

    #1997-01-15 전부터 지분을 가진 소유자 수
    before_1997 = sum(1 for r in rows if (r.get("ownshipChgDe") or "9999") < "1997-01-15")
    return {
        "owner_type": owner_type,
        "owner_code": owner_code,
        "owner_count": owner_count,
        "owners_before_1997": before_1997,
        "fallback": False,
    }

def _request_possession(pnu: str) -> dict:
    rows, total = vworld.get_possessions(pnu, POSSESSION_SAMPLE_ROWS)
    return _summarize_possessions(rows, total)

async def fetch_land_possession(pnu: str) -> dict:
    cached = await get_cached_possession(pnu)
    if cached:
        return cached

    try:
        result = await _run(vworld_executor, _request_possession, pnu)
        if not result.get("fallback"):
            await set_cached_possession(pnu, result)
        return result

    except Exception as err:
        logger.warning(f"[ Log ] : V-World 토지소유정보 오류 {pnu}: {err}")
        return dict(POSSESSION_EMPTY)

#=== 공동주택가격 ===#
APT_PRICE_MAX_PAGES = 20
APT_PRICE_PAGE_ROWS = 1000
APT_PRICE_EMPTY = {"year": None, "unit_count": 0, "official_total": 0.0, "area_prices": [], "fallback": True}

def _request_apartment_prices(pnu: str) -> dict:
    this_year = datetime.now().year
    for year in (this_year, this_year - 1):
        units: dict[tuple, float] = {}
        for page in range(1, APT_PRICE_MAX_PAGES + 1):
            rows = vworld.get_apart_housing_prices(pnu, year, page, APT_PRICE_PAGE_ROWS)
            for row in rows:
                if str(row.get("stdrYear")) != str(year):
                    continue
                unit = (row.get("aphusNm"), row.get("dongNm"), row.get("hoNm"), row.get("prvuseAr"))
                units[unit] = float(row.get("pblntfPc") or 0) / 10_000     # 원 → 만원
            if len(rows) < APT_PRICE_PAGE_ROWS:
                break
        if units:
            by_area: dict[float, list[float]] = {}
            for (_, _, _, area), price in units.items():
                by_area.setdefault(round(float(area or 0), 2), []).append(price)
            return {
                "year": year,
                "unit_count": len(units),
                "official_total": round(sum(units.values()), 1),
                "area_prices": [[a, round(statistics.median(v), 1), len(v)] for a, v in sorted(by_area.items())],
                "fallback": False,
            }
    return dict(APT_PRICE_EMPTY)

async def fetch_apartment_prices(pnu: str) -> dict:
    cached = await get_cached_apartment_prices(pnu)
    if cached:
        return cached
    try:
        result = await _run(vworld_executor, _request_apartment_prices, pnu)
        if not result.get("fallback"):
            await set_cached_apartment_prices(pnu, result)
        return result
    except Exception as err:
        logger.warning(f"[ Log ] : V-World 공동주택가격 오류 {pnu}: {err}")
        return dict(APT_PRICE_EMPTY)

#=== 개별주택가격 ===#
HOUSE_PRICE_EMPTY = {"year": None, "house_price": 0.0, "fallback": True}

def _request_house_price(pnu: str) -> dict:
    this_year = datetime.now().year
    for year in (this_year, this_year - 1):
        rows = vworld.get_indvd_housing_prices(pnu, year)
        houses = {
            (row.get("dongCode"), row.get("buldSn"), row.get("buldAllTotAr")): float(row.get("housePc") or 0) / 10_000
            for row in rows if str(row.get("stdrYear")) == str(year)
        }
        if houses:
            return {"year": year, "house_price": round(sum(houses.values()), 1), "fallback": False}
    return dict(HOUSE_PRICE_EMPTY)

async def fetch_house_price(pnu: str) -> dict:
    cached = await get_cached_house_price(pnu)
    if cached:
        return cached
    try:
        result = await _run(vworld_executor, _request_house_price, pnu)
        if not result.get("fallback"):
            await set_cached_house_price(pnu, result)
        return result
    except Exception as err:
        logger.warning(f"[ Log ] : V-World 개별주택가격 오류 {pnu}: {err}")
        return dict(HOUSE_PRICE_EMPTY)

#=== 건축물대장 (캐시 경유) ===#
async def fetch_building_cached(pnu: str) -> dict:
    cached = await get_cached_building(pnu)
    if cached:
        return cached

    ledger = await _run(building_hub_executor, fetch_building, pnu)
    data = asdict(ledger)

    #조회 자체가 실패한 경우는 캐시하지 않는다. 키 설정·일시 장애가 굳어버린다
    if not ledger.fallback:
        await set_cached_building(pnu, data)

    return data

#아파트 단지 정보
async def fetch_apartment_complex_cached(pnu: str) -> dict:
    cached = await get_cached_apartment(pnu)
    if cached:
        return cached
    data = asdict(await _run(building_hub_executor, fetch_apartment_complex, pnu))
    if not data.get("fallback"):
        await set_cached_apartment(pnu, data)
    return data


#집합건물 전유면적 합계 (캐시 경유) — /contribution 이 내 필지 하나에만 부른다.
#  대단지는 37페이지(9.7초)가 걸려 /zone 에서는 받지 않는다. 0(다 못 받음)은 캐시하지 않는다 (zone_service)
async def fetch_exclusive_total_cached(pnu: str) -> float:
    total = await get_cached_exclusive_total(pnu) or 0.0
    if not total:
        total = await _run(building_hub_executor, fetch_exclusive_total, pnu)
        await set_cached_exclusive_total(pnu, total)
    return total