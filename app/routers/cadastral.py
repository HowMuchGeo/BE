from fastapi import APIRouter
from app.clients import vworld
from app.schemas.cadastral.cadastral_request import CadastralRequest
from app.schemas.cadastral.cadastral_response import CadastralResponse
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
@router.post(
    "/cadastral",
    response_model = CadastralResponse,
    summary = "필지 데이터 조회"
)
async def get_vworld_cadastral(request: CadastralRequest):

    #요청값에서 geom_filter 추출
    geom_filter = request.geom_filter

    #BBOX 값 형식에 맞게 조정
    try:
        min_lng, min_lat, max_lng, max_lat = (
            value.strip() for value in geom_filter[geom_filter.index("(") + 1: geom_filter.rindex(")")].split(",")
        )
        box_str = f"BOX({min_lng},{min_lat},{max_lng},{max_lat})"
    except (ValueError, IndexError) as err:
        raise BadRequestException("geom_filter 는 BOX(경도,위도,경도,위도) 형식이어야 합니다.")

    try:
        #V-World 연속지적도 호출
        api_data = await vworld.get_cadastral_features(box_str)

        #API 성공여부 로깅
        logger.warning(f"[ Log ] : Cadastral API 호출 성공 {box_str}")

        #원하는 형식에 맞게 결과 반환
        feature_collection = (
            api_data.get("response", {})
            .get("result", {})
            .get("featureCollection", api_data)
        )

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

    #오류 발생시 기존 동작 유지
    except Exception as err:
        logger.warning(f"[ Log ] : Cadastral API 호출 실패 : {str(err)}")
        ServiceUnavailableException("Cadastral API 요청중 오류가 발생했습니다.")
        return None