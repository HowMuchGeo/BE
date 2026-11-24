from fastapi import APIRouter
from app.exceptions.exceptions_handler import NotFoundException, ServiceUnavailableException
from app.schemas.location.location_request import LocationRequest
from app.schemas.location.location_response import LocationResponse
from app.services import location_service
import logging

#주소 변환 API 라우터 설정
router = APIRouter(
    prefix="/api/v1",
    tags=["Location Transformation Router"]
)

#백엔드 Logger
logger = logging.getLogger(__name__)

#주소 → 좌표 변환 API 엔드포인트
@router.post(
    "/location",
    response_model=LocationResponse,
    summary="상세 주소 → 위도 · 경도 변환",
)
async def get_location_coordinate(request: LocationRequest):
    try:
        result = await location_service.geocode(request.address)
    except Exception as err:
        logger.warning(f"[ Log ] : Location API 호출 실패 : {request.address} : {err}")
        raise ServiceUnavailableException("주소 좌표 변환 중 오류가 발생했습니다.")

    if result is None:
        raise NotFoundException("입력한 주소의 좌표를 찾지 못했습니다. 도로명 또는 지번 주소를 확인해 주세요.")

    return result
