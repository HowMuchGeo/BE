from fastapi import APIRouter
from app.schemas.cadastral.cadastral_request import CadastralRequest
from app.schemas.cadastral.cadastral_response import CadastralResponse
from app.services import cadastral_service
from app.exceptions.exceptions_handler import BadRequestException, ServiceUnavailableException
import logging

#필지 데이터 반환 API 라우터 설정
router = APIRouter(
    prefix="/api/v1",
    tags=["Cadastral Router"]
)

#백엔드 Logger
logger = logging.getLogger(__name__)

#필지 데이터 반환 API 엔드포인트
#요청 BOX 의 중앙 기준 100m 박스와 겹치는 행정동 전체 필지를 반환
@router.post(
    "/cadastral",
    response_model = CadastralResponse,
    summary = "필지 데이터 조회 (화면 중앙 100m 와 겹치는 행정동 전체)"
)
async def get_vworld_cadastral(request: CadastralRequest):

    #BBOX 형식 검증
    try:
        cadastral_service.parse_box(request.geom_filter)
    except ValueError:
        raise BadRequestException("geom_filter 는 BOX(경도,위도,경도,위도) 형식이어야 합니다.")

    try:
        feature_collection = await cadastral_service.get_parcels_around(request.geom_filter)
    except Exception as err:
        logger.warning(f"[ Log ] : Cadastral API 호출 실패 : {str(err)}")
        raise ServiceUnavailableException("Cadastral API 요청중 오류가 발생했습니다.")

    return {
        "response": {
            "service": {
                "name": "data",
                "version": "2.0",
                "operation": "GetFeature",
                "time": "120(ms)"
            },
            "status": "OK",
            "result": {
                "featureCollection": feature_collection
            }
        }
    }
