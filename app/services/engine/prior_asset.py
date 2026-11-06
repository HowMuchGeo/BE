from dataclasses import dataclass, field

#구조별 (구조명, 내용연수, 최종연도 잔가율, 매년 상각률) — 건축물 시가표준액 조정기준
STRUCTURE_TABLE = [
    (("철골철근콘크리트", "철골철근", "통나무"),                      50, 0.20, 0.016),
    (("철근콘크리트", "라멘", "석구조", "석조", "프리캐스트", "목구조"), 40, 0.20, 0.020),
    (("철골", "스틸하우스", "연와", "보강콘크리트", "보강블록",
      "황토", "시멘트벽돌", "벽돌", "목조", "ALC", "와이어패널"),      30, 0.10, 0.030),
    (("시멘트블록", "블록", "경량철골", "조립식패널", "FRP"),          20, 0.10, 0.045),
    (("석회", "흙벽돌", "돌담", "토담", "철파이프", "컨테이너"),        10, 0.10, 0.090),
]

#구조 미상 기본값
DEFAULT_STRUCTURE = ("벽돌구조", 30, 0.10, 0.030)

@dataclass
class BuildingSpec:
    structure: str
    floor_area_m2: float
    elapsed_years: float
    cost_index: float = 1.0

#재조달원가 구조지수
STRUCTURE_COST_INDEX = [
    (("철골철근콘크리트",), 120),
    (("철근콘크리트", "라멘", "프리캐스트", "석구조", "석조", "연와"), 100),
    (("경량철골",), 60),
    (("철골",), 100),
    (("블록",), 60),
    (("벽돌", "조적"), 90),
    (("통나무",), 140),
    (("목",), 78),
]

DEFAULT_STRUCTURE_COST_INDEX = 90

#재조달원가 용도지수
APARTMENT_USE_INDEX = 110
HOUSE_USE_INDEX = 100
NEIGHBORHOOD_USE_INDEX = 117

def building_cost_index(structure: str, main_purpose: str = "", floors: int = 0, etc_purpose: str = "") -> float:
    name = (structure or "").replace(" ", "")
    structure_index = next(
        (index for keywords, index in STRUCTURE_COST_INDEX if any(k in name for k in keywords)),
        DEFAULT_STRUCTURE_COST_INDEX,
    )
    purpose = (main_purpose or "").replace(" ", "")
    if "공동주택" in purpose:
        is_apartment = "아파트" in (etc_purpose or "") or floors >= 5
        use_index = APARTMENT_USE_INDEX if is_apartment else HOUSE_USE_INDEX
    elif "근린생활" in purpose:
        use_index = NEIGHBORHOOD_USE_INDEX
    else:
        use_index = HOUSE_USE_INDEX
    return structure_index * use_index / (100 * APARTMENT_USE_INDEX)


#구조명 -> (내용연수, 최종연도 잔가율, 매년 상각률)
def structure_spec(structure: str) -> tuple[int, float, float]:
    name = (structure or "").replace(" ", "")
    for keywords, years, floor, annual in STRUCTURE_TABLE:
        if any(k in name for k in keywords):
            return years, floor, annual
    return DEFAULT_STRUCTURE[1], DEFAULT_STRUCTURE[2], DEFAULT_STRUCTURE[3]

#잔존율
def residual_rate(structure: str, elapsed_years: float) -> float:
    _, floor, annual = structure_spec(structure)
    return max(1.0 - annual * max(elapsed_years, 0.0), floor)

#건물분 = 재조달원가 × 잔존율
def building_value(building: BuildingSpec, replacement_cost_per_m2: float) -> float:
    return (
        building.floor_area_m2
        * replacement_cost_per_m2
        * building.cost_index
        * residual_rate(building.structure, building.elapsed_years)
    )

#토지분
def land_value(land_area_m2: float, price_per_m2: float, land_multiplier: float) -> float:
    return land_area_m2 * price_per_m2 / 10_000 * land_multiplier  # 원 → 만원

@dataclass
class PriorAsset:
    land: float
    building: float
    total: float
    ratio: float
    residual: float

#개인 종전자산 평가액
def estimate(
    land_area_m2: float,
    land_price_per_m2: float,
    land_multiplier: float,
    building: BuildingSpec | None,
    replacement_cost_per_m2: float,
    share: float = 1.0,
) -> PriorAsset:
    land = land_value(land_area_m2, land_price_per_m2, land_multiplier) * share
    if building is None:  # 나대지
        bld, residual = 0.0, 0.0
    else:
        bld = building_value(building, replacement_cost_per_m2) * share
        residual = residual_rate(building.structure, building.elapsed_years)

    total = land + bld
    official = land_area_m2 * land_price_per_m2 / 10_000 * share  # 보정 전 공시지가
    return PriorAsset(land, bld, total, total / official if official else 0.0, residual)

#지목별 토지 감액률
LAND_CATEGORY_RATE = {
    "도로": 1.0 / 3.0,
    "구거": 1.0 / 3.0,
}

#건물분 비중 경고 기준
BUILDING_SHARE_WARN = 0.40

#필지 단위 입력
@dataclass
class ParcelValuation:
    pnu: str
    land_area_m2: float
    land_price_per_m2: float
    structure: str = ""
    building_area_m2: float = 0.0
    elapsed_years: float = 0.0
    household_count: int = 1
    has_building: bool = False
    land_category: str = ""
    cost_index: float = 1.0
    owner_type: str = ""

    #주택 공시가격
    housing_kind: str = ""
    housing_price: float = 0.0
    housing_units: int = 0

#국공유 소유구분 키워드
PUBLIC_OWNER_KEYWORDS = ("국유", "도유", "시유", "군유", "구유")

def is_public_owner(owner_type: str) -> bool:
    name = (owner_type or "").replace(" ", "")
    return any(k in name for k in PUBLIC_OWNER_KEYWORDS)

#다가구 가구별 분양대상자 기준일
MULTI_HOUSEHOLD_SPLIT_BEFORE = "19970115"

def ordinance_member_count(
    main_purpose: str, approval_ymd: str, household_count: int, owner_count: int, owners_before_1997: int | None = None,
) -> int:
    households = max(int(household_count or 1), 1)
    if "단독주택" not in (main_purpose or ""):
        return households
    if owners_before_1997 is not None:
        return min(households, int(owners_before_1997)) if int(owners_before_1997) >= 2 else 1
    approved = (approval_ymd or "").strip()
    if approved and approved < MULTI_HOUSEHOLD_SPLIT_BEFORE and int(owner_count or 0) >= 2:
        return min(households, int(owner_count))
    return 1

@dataclass
class ParcelPriorAsset:
    pnu: str
    land: float
    building: float
    total: float
    residual: float
    household_count: int

@dataclass
class ZonePriorAsset:
    land_total: float
    building_total: float
    total: float
    official_total: float
    ratio: float
    member_count: int
    parcel_count: int
    building_parcel_count: int
    parcels: list[ParcelPriorAsset] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    #종전자산 필지 토지면적 합계
    land_area_m2: float = 0.0

    housing_total: float = 0.0
    housing_parcel_count: int = 0

    dae_land_total: float = 0.0
    dae_land_area_m2: float = 0.0


#구역 종전자산 총액 + 조합원 수
def aggregate_zone(
    parcels: list[ParcelValuation],
    land_multiplier: float,
    replacement_cost_per_m2: float,
    housing_rates: dict[str, float] | None = None,
    housing_multiplier: float = 1.0,
) -> ZonePriorAsset:
    rows: list[ParcelPriorAsset] = []
    warnings: list[str] = []
    land_total = building_total = official_total = 0.0
    land_area = 0.0
    member_count = 0
    building_parcel_count = 0
    no_price_count = 0
    discounted_count = 0
    unknown_road_count = 0
    public_count = 0
    public_area = 0.0
    housing_total = 0.0
    housing_parcel_count = 0
    dae_land_total = dae_land_area = 0.0

    for parcel in parcels:

        #국공유 단독 소유 필지 제외
        if is_public_owner(parcel.owner_type) and parcel.household_count <= 1:
            public_count += 1
            public_area += parcel.land_area_m2
            continue

        building = (
            BuildingSpec(parcel.structure, parcel.building_area_m2, parcel.elapsed_years, parcel.cost_index)
            if parcel.has_building and parcel.building_area_m2 > 0
            else None
        )

        #도로, 구거 감액
        category_rate = LAND_CATEGORY_RATE.get(parcel.land_category, 1.0)
        if category_rate < 1.0:
            discounted_count += 1
            if not parcel.owner_type:
                unknown_road_count += 1

        asset = estimate(
            land_area_m2=parcel.land_area_m2,
            land_price_per_m2=parcel.land_price_per_m2,
            land_multiplier=land_multiplier * category_rate,
            building=building,
            replacement_cost_per_m2=replacement_cost_per_m2,
        )

        #지목 '대' 필지 합계
        if parcel.land_category == "대":
            dae_land_total += asset.land
            dae_land_area += parcel.land_area_m2

        #주택 공시가격 / 현실화율
        rate = (housing_rates or {}).get(parcel.housing_kind)
        if rate and parcel.housing_price > 0:
            value = parcel.housing_price / rate * housing_multiplier
            asset = PriorAsset(0.0, 0.0, value, value / (parcel.land_area_m2 * parcel.land_price_per_m2 / 10_000)
                               if parcel.land_area_m2 * parcel.land_price_per_m2 else 0.0, asset.residual)
            housing_total += value
            housing_parcel_count += 1
        else:
            land_total += asset.land
            building_total += asset.building
            if building is not None:
                building_parcel_count += 1

        official_total += parcel.land_area_m2 * parcel.land_price_per_m2 / 10_000
        land_area += parcel.land_area_m2
        member_count += max(parcel.household_count, 1)

        #공시지가 없는 필지
        if parcel.land_price_per_m2 <= 0:
            no_price_count += 1

        rows.append(
            ParcelPriorAsset(
                pnu=parcel.pnu,
                land=asset.land,
                building=asset.building,
                total=asset.total,
                residual=asset.residual,
                household_count=max(parcel.household_count, 1),
            )
        )

    total = land_total + building_total + housing_total

    #건물분 0 경고
    if parcels and building_parcel_count == 0 and housing_parcel_count == 0:
        warnings.append(
            "건축물대장을 받아오지 못해 건물분이 0 입니다. "
            "분담금이 구역 평균값으로만 나옵니다 (개인별 차이가 반영되지 않습니다)."
        )

    #국공유지 제외 안내
    if public_count:
        warnings.append(
            f"{public_count}개 필지({public_area:,.0f}㎡)가 국·공유지라 종전자산과 조합원 수에서 뺐습니다 "
            "(조합원 자산이 아니다. 정비기반시설이면 무상양도, 그 밖이면 조합이 매입한다)."
        )

    #도로, 구거 감액 안내
    if discounted_count:
        message = (
            f"{discounted_count}개 필지가 지목상 도로·구거(사유지)라 토지분을 1/3 로 감액했습니다 "
            "(토지보상법 시행규칙 제26조, 사실상의 사도 기준). 사도법상 사도면 1/5 입니다."
        )
        if unknown_road_count:
            message += f" 그중 {unknown_road_count}개는 소유구분을 확인하지 못했습니다 — 국·공유지면 빼야 합니다."
        warnings.append(message)

    #공시지가 없는 필지 안내
    if no_price_count:
        warnings.append(
            f"{no_price_count}개 필지의 공시지가를 불러오지 못해 토지분을 0 으로 두었습니다. "
            "종전자산이 실제보다 낮게 잡혀 분담금이 과대평가됩니다."
        )

    #건물분 비중 상한 점검
    if total and building_total / total > BUILDING_SHARE_WARN:
        warnings.append(
            f"건물분이 종전자산의 {building_total / total:.0%} 입니다 "
            f"(노후주택 밀집지는 보통 10~20%). 아파트 단지 필지가 섞여 있지 않은지 확인해 주세요 — "
            "섞이면 종전자산이 과대평가되어 분담금이 실제보다 낮게 나옵니다."
        )

    return ZonePriorAsset(
        land_total=land_total,
        building_total=building_total,
        total=total,
        official_total=official_total,
        ratio=total / official_total if official_total else 0.0,
        member_count=max(member_count, 1),
        parcel_count=len(parcels) - public_count,
        building_parcel_count=building_parcel_count,
        parcels=rows,
        warnings=warnings,
        land_area_m2=land_area,
        housing_total=housing_total,
        housing_parcel_count=housing_parcel_count,
        dae_land_total=dae_land_total,
        dae_land_area_m2=dae_land_area,
    )