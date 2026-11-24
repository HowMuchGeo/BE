#주소 → 좌표(위도 · 경도) 변환 서비스
#  ① Kakao 주소 검색 (입력 그대로) → ② 동·호수 등 상세를 뗀 주소로 다시 검색 → ③ 장소(건물명) 검색
#  주소 검색은 "101동 1203호" 같은 상세가 붙으면 못 찾는 경우가 많아 ② 를 둔다.
#  좌표계는 WGS84 (EPSG:4326). Kakao 응답의 x = 경도, y = 위도
import logging
import re

import httpx

from app.cache import keys
from app.cache.store import redis_store
from app.clients import kakao_search

#백엔드 Logger
logger = logging.getLogger(__name__)

#Kakao 로컬 API 타임아웃 (초)
REQUEST_TIMEOUT = 5.0

#괄호 안 참고항목 : "테헤란로 152 (역삼동)"
_PARENTHESES = re.compile(r"\([^)]*\)")

#동 · 호 · 층 상세 토큰 : "101동", "B동", "1203호", "12층", "제3층", "302호실"
_DETAIL_TOKEN = re.compile(r"^제?[0-9A-Za-z]+(-[0-9]+)?(동|호|층|호실)$")


#=== 주소 정리 ===#
#상세(괄호 · 쉼표 뒤 · 동/호/층)를 뗀 기본 주소
def strip_detail(address: str) -> str:
    base = _PARENTHESES.sub(" ", address).split(",")[0]
    tokens = base.split()

    #뒤에서부터 상세 토큰만 뗀다 (가운데의 "역삼1동" 같은 행정동명은 건드리지 않는다)
    while len(tokens) > 1 and _DETAIL_TOKEN.match(tokens[-1]):
        tokens.pop()
    return " ".join(tokens)


#=== 응답 변환 ===#
def _from_address(document: dict, query: str) -> dict | None:
    try:
        longitude, latitude = float(document["x"]), float(document["y"])
    except (KeyError, TypeError, ValueError):
        return None

    jibun = document.get("address") or {}
    road = document.get("road_address") or {}
    return {
        "query": query,
        "address_name": document.get("address_name") or road.get("address_name") or jibun.get("address_name") or query,
        "road_address": road.get("address_name") or None,
        "jibun_address": jibun.get("address_name") or None,
        "building_name": road.get("building_name") or None,
        "b_code": jibun.get("b_code") or None,
        "latitude": latitude,
        "longitude": longitude,
        "match_type": "address",
    }


def _from_keyword(document: dict, query: str) -> dict | None:
    try:
        longitude, latitude = float(document["x"]), float(document["y"])
    except (KeyError, TypeError, ValueError):
        return None

    return {
        "query": query,
        "address_name": document.get("road_address_name") or document.get("address_name") or query,
        "road_address": document.get("road_address_name") or None,
        "jibun_address": document.get("address_name") or None,
        "building_name": document.get("place_name") or None,
        "b_code": None,
        "latitude": latitude,
        "longitude": longitude,
        "match_type": "keyword",
    }


#=== 진입점 ===#
#주소 → 좌표. 찾지 못하면 None, 외부 API 오류는 예외(ExternalAPIError · httpx.HTTPError)를 그대로 올린다
async def geocode(address: str) -> dict | None:
    cached = await redis_store.get_cache(keys.GEOCODE, address)
    if cached is not None:
        return cached

    base = strip_detail(address)
    queries = [address] if base == address else [address, base]

    result = None
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:

        #① · ② 주소 검색
        for query in queries:
            documents = await kakao_search.search_address(query, size=1, client=client)
            if documents:
                result = _from_address(documents[0], address)
                if result:
                    break

        #③ 장소 검색 (건물명 · 단지명)
        if result is None:
            for query in queries:
                documents = await kakao_search.search_keyword(query, size=1, client=client)
                if documents:
                    result = _from_keyword(documents[0], address)
                    if result:
                        break

    if result is None:
        logger.warning(f"[ Log ] : 주소 좌표 변환 실패 (검색 결과 없음) : {address}")
        return None

    await redis_store.set_cache(keys.GEOCODE, result, address)
    return result
