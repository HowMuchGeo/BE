from typing import Literal, Optional
from pydantic import BaseModel, Field

#주소 → 좌표 변환 응답 스키마 (좌표계 WGS84 / EPSG:4326)
class LocationResponse(BaseModel):
    query: str = Field(..., description="요청한 주소 (공백 정리 후)")
    address_name: str = Field(..., description="매칭된 대표 주소")
    road_address: Optional[str] = Field(None, description="도로명 주소")
    jibun_address: Optional[str] = Field(None, description="지번 주소")
    building_name: Optional[str] = Field(None, description="건물명 (있을 때만)")
    b_code: Optional[str] = Field(None, description="법정동 코드 10자리 (PNU 앞 10자리)")
    latitude: float = Field(..., description="위도 (y)")
    longitude: float = Field(..., description="경도 (x)")
    match_type: Literal["address", "keyword"] = Field(
        ..., description="address : 주소 검색으로 매칭 / keyword : 주소로 못 찾아 장소(건물명) 검색으로 매칭"
    )
