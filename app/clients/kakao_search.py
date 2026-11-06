import httpx
from app.clients.http import ExternalAPIError
from app.config.settings import settings

WEB_SEARCH_URL = "https://dapi.kakao.com/v2/search/web"

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