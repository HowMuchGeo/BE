from pydantic import BaseModel, Field, field_validator

#주소 → 좌표 변환 요청 스키마
class LocationRequest(BaseModel):
    address: str = Field(
        ...,
        min_length=2,
        max_length=200,
        description="변환할 상세 주소 (도로명 · 지번 · 건물명 모두 가능). 예: 서울 강남구 테헤란로 152",
        examples=["서울 강남구 테헤란로 152"],
    )

    #앞뒤 공백 제거 + 연속 공백을 한 칸으로
    @field_validator("address")
    @classmethod
    def normalize_address(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 2:
            raise ValueError("주소를 2자 이상 입력해 주세요.")
        return normalized
