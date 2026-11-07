import os

#환경변수 누락 예외
class MissingSettingError(RuntimeError):
    def __init__(self, key: str):
        super().__init__(f"[ Backend ] : {key} 환경 변수가 설정되지 않았습니다.")
        self.key = key

#환경변수 접근 객체
class Settings:

    #서버 기동에 반드시 필요한 값
    REQUIRED_KEYS = (
        "VWORLD_API_KEY",
        "VWORLD_DOMAIN",
        "PROXY_URL",
        "KAKAO_API_KEY",
        "KAKAO_PAYMENT_SECERT_KEY",
        "KAKAO_PAYMENT_CID",
        "KAKAO_PAYMENT_BASE_URL",
        "BACKEND_URL",
    )

    @staticmethod
    def _required(key: str) -> str:
        value = os.getenv(key)
        if not value:
            raise MissingSettingError(key)
        return value

    @staticmethod
    def _optional(*keys: str) -> str | None:
        for key in keys:
            value = os.getenv(key)
            if value:
                return value
        return None

    #누락된 필수값을 한 번에 모아 예외를 던짐
    def validate_required(self) -> None:
        missing = [key for key in self.REQUIRED_KEYS if not os.getenv(key)]
        if missing:
            raise MissingSettingError(", ".join(missing))

    #=== V-World ===#
    @property
    def vworld_api_key(self) -> str:
        return self._required("VWORLD_API_KEY")

    @property
    def vworld_domain(self) -> str:
        return self._required("VWORLD_DOMAIN")

    #V-World 프록시
    @property
    def proxy_url(self) -> str:
        return self._required("PROXY_URL").rstrip("/")

    #=== 공공데이터포털 ===#
    #국토부 실거래가. 없으면 실거래 보정 없이 계산
    @property
    def molit_api_key(self) -> str | None:
        return self._optional("MOLIT_API_KEY")

    #건축HUB 건축물대장. 없으면 건물분 없이(나대지로) 계산
    @property
    def bldrgst_api_key(self) -> str | None:
        return self._optional("BLDRGST_API_KEY", "BUILDING_LEDGER_API_KEY")

    #=== Kakao ===#
    @property
    def kakao_api_key(self) -> str:
        return self._required("KAKAO_API_KEY")

    @property
    def kakao_payment_secret_key(self) -> str:
        return self._required("KAKAO_PAYMENT_SECERT_KEY")

    @property
    def kakao_payment_cid(self) -> str:
        return self._required("KAKAO_PAYMENT_CID")

    @property
    def kakao_payment_base_url(self) -> str:
        return self._required("KAKAO_PAYMENT_BASE_URL").rstrip("/")

    #=== Service URL ===#
    @property
    def backend_url(self) -> str:
        return self._required("BACKEND_URL").rstrip("/")

    @property
    def frontend_url(self) -> str:
        return (os.getenv("FRONTEND_URL") or "").rstrip("/")

settings = Settings()