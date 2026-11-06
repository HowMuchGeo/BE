from dataclasses import dataclass, field
from app.services.engine.prior_asset import is_public_owner

REDEVELOPMENT = "재개발"
RECONSTRUCTION = "재건축"

#판정 제외 지목
INFRA_CATEGORIES = ("도로", "구거", "하천", "제방", "유지", "공원")

#건물 없는 소필지 기준
MINOR_PARCEL_SHARE = 0.05

def is_apartment(main_purpose: str, etc_purpose: str, floors: int) -> bool:
    return "공동주택" in (main_purpose or "") and ("아파트" in (etc_purpose or "") or int(floors or 0) >= 5)

@dataclass
class ParcelUse:
    pnu: str
    has_building: bool
    main_purpose: str = ""
    etc_purpose: str = ""
    floors: int = 0
    owner_type: str = ""
    land_category: str = ""
    land_area_m2: float = 0.0

@dataclass
class ProjectTypeResult:
    project_type: str
    reason: str
    apartment_pnus: list[str] = field(default_factory=list)
    annex_pnus: list[str] = field(default_factory=list)
    ignored_pnus: list[str] = field(default_factory=list)
    other_pnus: list[str] = field(default_factory=list)
    minor_pnus: list[str] = field(default_factory=list)

#사업 유형 판정
def classify_project(parcels: list[ParcelUse], annex: set[str]) -> ProjectTypeResult:
    result = ProjectTypeResult(REDEVELOPMENT, "")
    for p in parcels:
        if p.has_building and is_apartment(p.main_purpose, p.etc_purpose, p.floors):
            result.apartment_pnus.append(p.pnu)
        elif not p.has_building and p.pnu in annex:
            result.annex_pnus.append(p.pnu)
        elif is_public_owner(p.owner_type) or p.land_category in INFRA_CATEGORIES:
            result.ignored_pnus.append(p.pnu)
        else:
            result.other_pnus.append(p.pnu)

    #건물 없는 소필지만 섞였으면 재건축 유지
    total_area = sum(p.land_area_m2 for p in parcels)
    others = [p for p in parcels if p.pnu in result.other_pnus]
    minor_area = sum(p.land_area_m2 for p in others)
    if (
        result.apartment_pnus and others and total_area > 0
        and all(not p.has_building for p in others)
        and minor_area < MINOR_PARCEL_SHARE * total_area
    ):
        result.minor_pnus = [p.pnu for p in others]
        result.other_pnus = []

    excluded = f" · 국공유·기반시설 {len(result.ignored_pnus)}필지는 판정에서 뺌" if result.ignored_pnus else ""
    if result.minor_pnus:
        excluded += f" · 건물 없는 소필지 {len(result.minor_pnus)}개(선택 면적의 {minor_area / total_area:.1%})는 단지 땅으로 봄"
    if result.apartment_pnus and not result.other_pnus:
        annex_text = f" + 부속지번 {len(result.annex_pnus)}필지" if result.annex_pnus else ""
        result.project_type = RECONSTRUCTION
        result.reason = f"아파트 단지 필지 {len(result.apartment_pnus)}개{annex_text}만 선택{excluded}"
    elif result.apartment_pnus:
        result.reason = f"아파트 외 필지 {len(result.other_pnus)}개가 섞여 재개발로 계산{excluded}"
    else:
        result.reason = f"아파트 단지가 없어 재개발로 계산{excluded}"
    return result