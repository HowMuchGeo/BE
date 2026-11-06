import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

#재시도 대상 상태코드
RETRY_STATUS = (429, 500, 502, 503, 504)

#외부 API 호출 실패 공통 예외
class ExternalAPIError(Exception):
    def __init__(self, api: str, message: str, status_code: int | None = None, body=None):
        super().__init__(f"[{api}] {message}")
        self.api = api
        self.status_code = status_code
        self.body = body

#재시도가 걸린 requests 세션
def build_session(total: int, backoff_factor: float, raise_on_status: bool = True) -> requests.Session:
    session = requests.Session()
    session.mount(
        "https://",
        HTTPAdapter(
            max_retries=Retry(
                total=total,
                backoff_factor=backoff_factor,
                status_forcelist=RETRY_STATUS,
                allowed_methods=("GET",),
                raise_on_status=raise_on_status,
            )
        ),
    )
    return session

#응답
def parse_json(response: requests.Response, api: str) -> dict:
    content_type = response.headers.get("Content-Type", "unknown")
    if not response.ok:
        raise ExternalAPIError(
            api,
            f"returned HTTP {response.status_code} (content-type={content_type}, bytes={len(response.content)})",
            status_code=response.status_code,
        )
    try:
        data = response.json()
    except requests.exceptions.JSONDecodeError as err:
        body_preview = " ".join(response.text.split())[:300]
        raise ExternalAPIError(
            api,
            f"returned a non-JSON response (HTTP {response.status_code}, content-type={content_type}, "
            f"bytes={len(response.content)}, body={body_preview!r})",
            status_code=response.status_code,
        ) from err
    if not isinstance(data, dict):
        raise ExternalAPIError(api, "returned JSON with an unexpected top-level type", status_code=response.status_code)
    return data