import httpx
import time
from app.clients.http import ExternalAPIError, build_session, parse_json
from app.config.settings import settings
from urllib.parse import urlencode

#재시도 세션
_SESSION = build_session(total=4, backoff_factor=0.3)

#호출 간격(초). 쉬지 않고 던지면 서버가 연결을 끊어 재시도 대기가 붙고 오히려 느려진다
CALL_GAP = 0.05

#=== 공통 ===#
def _ned_get(endpoint: str, params: dict, timeout: float = 5, gap: bool = True) -> dict:
    if gap:
        time.sleep(CALL_GAP)
    response = _SESSION.get(
        f"{settings.proxy_url}/ned/data/{endpoint}",
        params={
            "key": settings.vworld_api_key,
            "domain": settings.vworld_domain,
            "format": "json",
            **params,
        },
        timeout=timeout,
    )
    return parse_json(response, endpoint)

#응답의 field 목록. 1건이면 dict 로 오므로 list 로 맞춘다
def _fields(root: dict) -> list[dict]:
    rows = (root or {}).get("field", [])
    return [rows] if isinstance(rows, dict) else rows

#루트 키 이름이 엔드포인트마다 달라 "field" 를 가진 첫 dict 를 루트로 본다
def _first_root(data: dict) -> dict:
    return next((v for v in data.values() if isinstance(v, dict) and "field" in v), {})

#=== NED ===#
#개별공시지가 (연도별 이력 행)
def get_indvd_land_prices(pnu: str) -> list[dict]:
    data = _ned_get("getIndvdLandPrice", {"pnu": pnu, "numOfRows": 30, "pageNo": 1}, gap=False)
    return _fields(data.get("indvdLandPrices", {}))

#토지특성 (연도별 이력 행)
def get_land_characteristics(pnu: str) -> list[dict]:
    data = _ned_get("getLandCharacteristics", {"pnu": pnu, "numOfRows": 30, "pageNo": 1})
    return _fields(data.get("landCharacteristicss", {}))

#토지소유정보 → (행 목록, totalCount)
def get_possessions(pnu: str, rows: int) -> tuple[list[dict], int | None]:
    data = _ned_get("getPossessionAttr", {"pnu": pnu, "numOfRows": rows, "pageNo": 1})
    root = data.get("possessions", {})
    total = root.get("totalCount")
    return _fields(root), (int(total) if total else None)

#공동주택가격 한 페이지
def get_apart_housing_prices(pnu: str, year: int, page: int, rows: int = 1000) -> list[dict]:
    data = _ned_get(
        "getApartHousingPriceAttr",
        {"pnu": pnu, "stdrYear": str(year), "numOfRows": rows, "pageNo": page},
        timeout=10,
    )
    return _fields(_first_root(data))

#개별주택가격
def get_indvd_housing_prices(pnu: str, year: int, rows: int = 20) -> list[dict]:
    data = _ned_get(
        "getIndvdHousingPriceAttr",
        {"pnu": pnu, "stdrYear": str(year), "numOfRows": rows, "pageNo": 1},
        timeout=10,
    )
    return _fields(_first_root(data))

#=== Data (지적도) ===#
#연속지적도 GetFeature (box_str = "BOX(경도,위도,경도,위도)")
async def get_cadastral_features(box_str: str, size: int = 1000) -> dict:
    params = {
        "service": "data",
        "request": "GetFeature",
        "data": "lp_pa_cbnd_bubun",
        "key": settings.vworld_api_key,
        "domain": settings.vworld_domain,
        "format": "json",
        "geomFilter": box_str,
        "epsg": "4326",
        "size": size,
    }

    #쿼리 인코딩은 기존 방식(urlencode)을 그대로 쓴다 — BOX(…) 의 괄호·쉼표 인코딩이 바뀌지 않도록
    url = f"{settings.proxy_url}/req/data?{urlencode(params)}"

    #기존 동작 유지 : TLS 검증을 끈 상태 (verify=False). 프록시 인증서 확인 후 켜는 것을 권장
    async with httpx.AsyncClient(verify=False) as client:
        response = await client.get(url, timeout=5.0)

    try:
        return response.json()
    except ValueError as err:
        raise ExternalAPIError("GetFeature", f"non-JSON response (HTTP {response.status_code})",
                               status_code=response.status_code) from err