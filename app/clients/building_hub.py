import threading
import time
from app.clients.http import ExternalAPIError, build_session
from app.config.settings import settings

BASE_URL = "https://apis.data.go.kr/1613000/BldRgstHubService"
TITLE = "getBrTitleInfo"
EXPOS = "getBrExposPubuseAreaInfo"
RECAP = "getBrRecapTitleInfo"
ATCH = "getBrAtchJibunInfo"

#numOfRows 상한
API_MAX_ROWS = 100

#호출 사이 최소 간격
CALL_GAP = 0.12

#200 인데 본문이 비거나 JSON 이 아닌 경우 재호출 횟수
JSON_RETRY = 3

#건축HUB의 고질적 503 SERVICETIMEOUT_ERROR 대비용 재호출
_SESSION = build_session(total=5, backoff_factor=0.6, raise_on_status=False)

#호출 간격 관리
_lock = threading.Lock()
_last_call = 0.0

def _throttle() -> None:
    wait = CALL_GAP - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)

def _get(endpoint: str, params: dict):
    global _last_call
    with _lock:
        _throttle()
        try:
            return _SESSION.get(f"{BASE_URL}/{endpoint}", params=params, timeout=15)
        finally:
            _last_call = time.monotonic()

#한 페이지
def _page(endpoint: str, params: dict, page_no: int, rows_per_page: int) -> tuple[list[dict], int]:
    call = {
        **params,
        "serviceKey": settings.bldrgst_api_key,
        "_type": "json",
        "numOfRows": rows_per_page,
        "pageNo": page_no,
    }

    payload = None
    last_error: Exception | None = None
    for attempt in range(JSON_RETRY):
        if attempt:
            time.sleep(CALL_GAP * (attempt + 1))
        response = _get(endpoint, call)
        try:
            payload = response.json()
        except ValueError as err:
            #200 인데 본문이 비었거나 JSON 이 아님
            last_error = err
            payload = None
            continue

        #정상 응답은 "response" 키를 가짐
        if "response" in payload:
            break

        fault = (payload.get("OpenAPI_ServiceResponse") or {}).get("cmmMsgHeader") or {}
        last_error = ExternalAPIError(
            endpoint,
            f"{fault.get('errMsg') or 'UNKNOWN'} / {fault.get('returnAuthMsg') or response.status_code}",
            status_code=response.status_code,
        )
        payload = None

    if payload is None:
        raise last_error or ExternalAPIError(endpoint, "건축물대장 응답을 받지 못했습니다")

    body = payload.get("response", {}).get("body", {})

    try:
        total = int(body.get("totalCount") or 0)
    except (TypeError, ValueError):
        total = 0

    items = body.get("items") or {}

    #건수가 0 이면 items 가 빈 문자열로 오기도 함
    if not isinstance(items, dict):
        return [], total

    rows = items.get("item")
    if rows is None:
        return [], total
    return (rows if isinstance(rows, list) else [rows]), total

#전체 페이지를 모아서 돌려줌
def _rows(endpoint: str, params: dict, max_pages: int) -> tuple[list[dict], int]:
    collected, total = _page(endpoint, params, 1, API_MAX_ROWS)
    if not collected:
        return [], total

    pages = min(-(-total // API_MAX_ROWS), max_pages)
    for page_no in range(2, pages + 1):
        more, _ = _page(endpoint, params, page_no, API_MAX_ROWS)
        if not more:
            break
        collected += more

    return collected, total

#params
def get_titles(params: dict, max_pages: int) -> tuple[list[dict], int]:
    return _rows(TITLE, params, max_pages)

def get_expos_pubuse_areas(params: dict, max_pages: int) -> tuple[list[dict], int]:
    return _rows(EXPOS, params, max_pages)

def get_recap_titles(params: dict, max_pages: int = 1) -> tuple[list[dict], int]:
    return _rows(RECAP, params, max_pages)

def get_atch_jibuns(params: dict, max_pages: int) -> tuple[list[dict], int]:
    return _rows(ATCH, params, max_pages)