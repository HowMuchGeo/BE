#필지(연속지적도) 조회 서비스
#화면 중앙 기준 반경 100m 박스와 겹치는 행정동을 찾고, 그 행정동들 안의 필지 전체를 반환한다
#
#- 행정동 경계 : app/services/admin_dong.py (통계청 SGIS 경계, 로컬 파일)
#- 필지 조회   : V-World 연속지적도를 고정 격자 타일 단위로 조회 · 캐시 → 필지 대표점이 행정동 안에 있는 것만 남긴다
import asyncio
import logging
import math
import re

from app.cache import keys
from app.cache.store import redis_store
from app.clients import vworld
from app.services.admin_dong import AdminDong, get_index
from app.utils.geo import Box, boxes_overlap, geometry_bbox, representative_point

#백엔드 Logger
logger = logging.getLogger(__name__)

#화면 중앙 기준 조회 반경 (m)
SEARCH_RADIUS_M = 100

#위도 1도당 거리 (m). 경도 1도 거리는 cos(위도) 를 곱해서 구한다
METERS_PER_DEG_LAT = 111_320

#필지 조회 타일 크기 (도). 위도 37도 기준 약 440m x 445m
TILE_LNG_DEG = 0.005
TILE_LAT_DEG = 0.004

#요청 1회 최대 타일 수. 넘으면 화면 중앙에서 가까운 타일부터 사용 (농어촌 면 단위처럼 큰 행정동 방어)
MAX_TILES = 80

#V-World 페이지 크기 (최대 1000) · 타일당 최대 페이지 수
PAGE_SIZE = 1000
MAX_PAGES_PER_TILE = 10

#V-World 동시 호출 수
_VWORLD_CONCURRENCY = asyncio.Semaphore(4)

_BOX_PATTERN = re.compile(r"^\s*BOX\s*\((.*)\)\s*$", re.IGNORECASE)

Tile = tuple[int, int]


#=== 좌표 계산 ===#
#"BOX(경도,위도,경도,위도)" → (min_lng, min_lat, max_lng, max_lat). 형식이 틀리면 ValueError
def parse_box(geom_filter: str) -> Box:
    match = _BOX_PATTERN.match(geom_filter or "")
    if not match:
        raise ValueError("BOX(...) 형식이 아닙니다.")

    values = [float(value) for value in match.group(1).split(",")]
    if len(values) != 4:
        raise ValueError("BOX 좌표는 4개여야 합니다.")

    min_lng, min_lat, max_lng, max_lat = values
    return min_lng, min_lat, max_lng, max_lat


def box_center(box: Box) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


#중심 좌표에서 반경 radius_m 만큼의 정사각형 BOX
def box_around(lng: float, lat: float, radius_m: float = SEARCH_RADIUS_M) -> Box:
    d_lat = radius_m / METERS_PER_DEG_LAT
    d_lng = radius_m / (METERS_PER_DEG_LAT * math.cos(math.radians(lat)))
    return lng - d_lng, lat - d_lat, lng + d_lng, lat + d_lat


def box_to_filter(box: Box) -> str:
    return f"BOX({box[0]:.6f},{box[1]:.6f},{box[2]:.6f},{box[3]:.6f})"


#=== 타일 ===#
def tile_box(tile: Tile) -> Box:
    x, y = tile
    return x * TILE_LNG_DEG, y * TILE_LAT_DEG, (x + 1) * TILE_LNG_DEG, (y + 1) * TILE_LAT_DEG


def tiles_in_box(box: Box) -> list[Tile]:
    x0, y0 = math.floor(box[0] / TILE_LNG_DEG), math.floor(box[1] / TILE_LAT_DEG)
    x1, y1 = math.floor(box[2] / TILE_LNG_DEG), math.floor(box[3] / TILE_LAT_DEG)
    return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


#행정동 폴리곤과 실제로 겹치는 타일만
def tiles_for_dong(dong: AdminDong) -> list[Tile]:
    return [tile for tile in tiles_in_box(dong.bbox) if dong.intersects_box(tile_box(tile))]


#=== V-World 응답 파싱 ===#
def _response(api_data: dict) -> dict:
    return (api_data or {}).get("response", {}) or {}


def _features(api_data: dict) -> list[dict]:
    collection = _response(api_data).get("result", {}).get("featureCollection") or api_data or {}
    return collection.get("features", []) or []


def _total_pages(api_data: dict) -> int:
    try:
        return int(_response(api_data).get("page", {}).get("total", 1))
    except (TypeError, ValueError):
        return 1


def _pnu(feature: dict) -> str:
    return str((feature.get("properties") or {}).get("pnu", ""))


#중복 제거용 키. pnu 가 없으면 feature id 로 대체
def _feature_key(feature: dict) -> str:
    return _pnu(feature) or str(feature.get("id", id(feature)))


#=== V-World 호출 ===#
async def _call(geom_filter: str, page: int) -> dict:
    async with _VWORLD_CONCURRENCY:
        return await vworld.get_cadastral_features(geom_filter, page=page, size=PAGE_SIZE)


#타일 하나의 필지. Redis 캐시 → 없으면 V-World 페이지 순회 후 저장
async def fetch_tile_features(tile: Tile) -> list[dict]:
    cached = await redis_store.get_cache(keys.CADASTRAL_TILE, *tile)
    if cached is not None:
        return cached

    geom_filter = box_to_filter(tile_box(tile))
    first = await _call(geom_filter, 1)

    total_pages = _total_pages(first)
    if total_pages > MAX_PAGES_PER_TILE:
        logger.warning(f"[ Log ] : 타일 {tile} 필지 페이지 {total_pages} 개 중 {MAX_PAGES_PER_TILE} 개만 조회")
        total_pages = MAX_PAGES_PER_TILE

    rest = await asyncio.gather(*(_call(geom_filter, page) for page in range(2, total_pages + 1)))
    features = [feature for data in (first, *rest) for feature in _features(data)]

    await redis_store.set_cache(keys.CADASTRAL_TILE, features, *tile)
    return features


#=== 진입점 ===#
#화면 BOX → 중앙 100m 박스와 겹치는 행정동 전체 필지 (GeoJSON FeatureCollection)
#- 각 필지 properties 에 adm_cd / adm_nm (행정동 코드 · 이름) 추가
#- FeatureCollection 에 admin_dongs (확장한 행정동 목록) 추가
#geom_filter 형식이 틀리면 ValueError, 타일 조회가 전부 실패하면 예외
async def get_parcels_around(geom_filter: str) -> dict:
    lng, lat = box_center(parse_box(geom_filter))
    search_box = box_around(lng, lat)

    dongs = get_index().intersecting(search_box)

    #행정동 타일 + 100m 박스 타일 (경계 데이터 밖 · 바다 등에서도 100m 안 필지는 보이도록)
    tiles = set(tiles_in_box(search_box))
    for dong in dongs:
        tiles.update(tiles_for_dong(dong))

    ordered = sorted(tiles, key=lambda t: _tile_distance(t, lng, lat))
    if len(ordered) > MAX_TILES:
        logger.warning(f"[ Log ] : 필지 타일 {len(ordered)} 개 중 가까운 {MAX_TILES} 개만 조회")
        ordered = ordered[:MAX_TILES]

    results = await asyncio.gather(*(fetch_tile_features(tile) for tile in ordered), return_exceptions=True)
    failed = [tile for tile, result in zip(ordered, results) if isinstance(result, BaseException)]
    for tile, result in zip(ordered, results):
        if isinstance(result, BaseException):
            logger.warning(f"[ Log ] : 필지 타일 {tile} 조회 실패 : {result}")
    if len(failed) == len(ordered):
        raise RuntimeError("필지 타일 조회가 모두 실패했습니다.")

    merged: dict[str, dict] = {}
    for result in results:
        if isinstance(result, BaseException):
            continue
        for feature in result:
            key = _feature_key(feature)
            if key in merged:
                continue
            dong = _owner_dong(feature, dongs)
            if dong is None and not _overlaps(feature, search_box):
                continue
            properties = dict(feature.get("properties") or {})
            properties["adm_cd"] = dong.code if dong else None
            properties["adm_nm"] = dong.name if dong else None
            merged[key] = {**feature, "properties": properties}

    logger.warning(
        f"[ Log ] : 필지 조회 {box_to_filter(search_box)} → 행정동 {[d.name for d in dongs]} "
        f"/ 타일 {len(ordered)} 개 / 총 {len(merged)} 건"
    )
    return {
        "type": "FeatureCollection",
        "admin_dongs": [{"code": d.code, "name": d.name} for d in dongs],
        "features": list(merged.values()),
    }


def _tile_distance(tile: Tile, lng: float, lat: float) -> float:
    cx, cy = box_center(tile_box(tile))
    return ((cx - lng) * math.cos(math.radians(lat))) ** 2 + (cy - lat) ** 2


#필지 대표점이 속한 행정동
def _owner_dong(feature: dict, dongs: list[AdminDong]) -> AdminDong | None:
    point = representative_point(feature.get("geometry"))
    if point is None:
        return None
    return next((dong for dong in dongs if dong.contains(*point)), None)


def _overlaps(feature: dict, box: Box) -> bool:
    bbox = geometry_bbox(feature.get("geometry"))
    return bbox is not None and boxes_overlap(bbox, box)
