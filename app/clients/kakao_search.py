import httpx
from app.clients.http import ExternalAPIError
from app.config.settings import settings

WEB_SEARCH_URL = "https://dapi.kakao.com/v2/search/web"
ADDRESS_SEARCH_URL = "https://dapi.kakao.com/v2/local/search/address.json"
KEYWORD_SEARCH_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"

#로컬 API 타임아웃 (초)
LOCAL_TIMEOUT = 5.0

def _headers(api_key: str | None) -> dict:
    return {"Authorization": f"KakaoAK {api_key or settings.kakao_api_key}"}

def _params(query: str, size: int, sort: str) -> dict:
    return {"query": query, "sort": sort, "size": size}

def _documents(response: httpx.Response) -> list[dict]:
    if response.status_code != 200:
        raise ExternalAPIError("kakao-web-search", f"HTTP {response.status_code}", status_code=response.status_code)
    return response.json().get("documents", [])

#비동기 검색 (API 서버용)
async def search_web(
    query: str,
    size: int = 10,
    sort: str = "recency",
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
) -> list[dict]:
    if client is None:
        async with httpx.AsyncClient() as own_client:
            return await search_web(query, size, sort, own_client, api_key)
    response = await client.get(WEB_SEARCH_URL, params=_params(query, size, sort), headers=_headers(api_key))
    return _documents(response)

#동기 검색 (스크립트용)
def search_web_sync(query: str, size: int = 10, sort: str = "recency", api_key: str | None = None, timeout: float = 10) -> list[dict]:
    response = httpx.get(WEB_SEARCH_URL, params=_params(query, size, sort), headers=_headers(api_key), timeout=timeout)
    return _documents(response)

#=== 로컬 (주소 · 장소 검색) ===#
async def _local_get(url: str, api: str, params: dict, client: httpx.AsyncClient | None, api_key: str | None) -> list[dict]:
    if client is None:
        async with httpx.AsyncClient(timeout=LOCAL_TIMEOUT) as own_client:
            return await _local_get(url, api, params, own_client, api_key)
    response = await client.get(url, params=params, headers=_headers(api_key))
    if response.status_code != 200:
        raise ExternalAPIError(api, f"HTTP {response.status_code}", status_code=response.status_code)
    return response.json().get("documents", [])

#주소 검색 (도로명 · 지번). 좌표는 x = 경도, y = 위도 (문자열)
async def search_address(
    query: str,
    size: int = 1,
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
) -> list[dict]:
    return await _local_get(ADDRESS_SEARCH_URL, "kakao-address-search", {"query": query, "size": size}, client, api_key)

#장소(키워드) 검색 — 건물명 · 단지명처럼 주소가 아닌 입력용
async def search_keyword(
    query: str,
    size: int = 1,
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
) -> list[dict]:
    return await _local_get(KEYWORD_SEARCH_URL, "kakao-keyword-search", {"query": query, "size": size}, client, api_key)
