from app.exceptions.exceptions_handler import ServiceUnavailableException
from app.schemas.news.news_request import NewsRequest
from app.schemas.news.news_response import NewsResponse
from app.services import news_service
from fastapi import APIRouter, status
import logging
from typing import List

#뉴스 조회 라우터
router = APIRouter(
    prefix="/api/v1",
    tags=["News Router"]
)

#백엔드 Logger
logger = logging.getLogger(__name__)

#뉴스 조회 API 엔드포인트
@router.post(
    "/news",
    response_model=List[NewsResponse],
    status_code = status.HTTP_200_OK,
)
async def get_region_news(request: NewsRequest):
    try:
        logger.warning(f"[ Log ] : News API 호출 시도 : {str(request.query)}")
        return await news_service.get_region_news(request.query)

    #예외 발생시 Service Unavailable Exception 발생
    except Exception:
        logger.warning(f"[ Log ] : News API 호출 실패")
        raise ServiceUnavailableException
