import html
import logging
import re
import httpx
from app.clients import kakao_search
from app.clients.http import ExternalAPIError

#백엔드 Logger
logger = logging.getLogger(__name__)

#검색어별 결과 수
NEWS_SIZE = 8

#HTML 태그 제거 + HTML 엔티티 복원
def clean_html(text: str | None) -> str:
    if not text:
        return ""
    return html.unescape(re.sub(r'<[^>]+>', '', text))

#검색 결과
def _clean_documents(documents: list[dict], excluded_prefixes: tuple[str, ...]) -> list[dict]:
    cleaned = []
    for item in documents:
        url = item.get("url") or ""
        if any(clean_html(url[:len(prefix)]) == prefix for prefix in excluded_prefixes):
            continue
        if clean_html(item.get("title")).endswith("pdf"):
            continue
        cleaned.append({
            "title": clean_html(item.get("title")),
            "contents": clean_html(item.get("contents")),
            "url": item.get("url"),
            "published_at": item.get("datetime"),
        })
    return cleaned

#"{지역} 재개발" + "{지역} 재건축" 최신 뉴스
async def get_region_news(query: str) -> list[dict]:
    #기존 동작 유지
    searches = [
        (f"{query} 재개발", ("https://namu", "https://gall.dcinside.com")),
        (f"{query} 재건축", ("https://namu",)),
    ]

    documents: list[dict] = []
    async with httpx.AsyncClient() as client:
        for keyword, excluded in searches:
            try:
                raw = await kakao_search.search_web(keyword, size=NEWS_SIZE, client=client)
            except ExternalAPIError as err:
                logger.warning(f"[ Log ] : News API 응답 오류 : {keyword} : {err}")
                continue
            documents += _clean_documents(raw, excluded)

    return documents