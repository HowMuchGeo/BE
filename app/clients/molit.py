import xml.etree.ElementTree as ET
from app.clients.http import ExternalAPIError, build_session
from app.config.settings import settings

API_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"

#한 달치 최대 행 수
MAX_ROWS = 1000

#재시도 세션 (기존 urllib 호출에는 재시도가 없었다)
_SESSION = build_session(total=3, backoff_factor=0.5)

#서비스 키. 없으면 ExternalAPIError
def _service_key(service_key: str | None) -> str:
    key = service_key or settings.molit_api_key
    if not key:
        raise ExternalAPIError("RTMSDataSvcAptTradeDev", "MOLIT_API_KEY 가 없습니다. .env 를 확인하세요.")
    return key

#한 달(deal_ymd = "YYYYMM") 거래 원본 행 목록
def get_apt_trades(lawd_cd: str, deal_ymd: str, service_key: str | None = None) -> list[dict]:
    response = _SESSION.get(
        API_URL,
        params={
            "serviceKey": _service_key(service_key),
            "LAWD_CD": lawd_cd,
            "DEAL_YMD": deal_ymd,
            "numOfRows": str(MAX_ROWS),
            "pageNo": "1",
        },
        timeout=30,
    )
    if not response.ok:
        raise ExternalAPIError("RTMSDataSvcAptTradeDev", f"HTTP {response.status_code}", status_code=response.status_code)

    try:
        root = ET.fromstring(response.content.decode())
    except ET.ParseError as err:
        raise ExternalAPIError("RTMSDataSvcAptTradeDev", f"XML 파싱 실패 : {err}") from err

    return [{child.tag: (child.text or "").strip() for child in item} for item in root.iter("item")]