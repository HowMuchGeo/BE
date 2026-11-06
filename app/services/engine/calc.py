from dataclasses import dataclass, replace
from app.services.engine.prior_asset import BuildingSpec, building_value
from app.services.engine.rental_cost import (
    UPLIFT_BASIS,
    UPLIFT_RATIO,
    takeover_basement_price_per_m2,
    takeover_price_per_m2,
    uplift_takeover_basement_price_per_m2,
    uplift_takeover_price_per_m2,
)
from app.services.engine.schema import(
    PER_PYEONG_TO_PER_M2,
    ProjectParams,
    ProjectResult,
    ProjectType,
    ParcelInfo,
    ZoneSummary,
    OwnerInput,
    UnitType,
    UnitMix,
    ContributionResult
)

#개인 종전자산
def estimate_prior_asset(owner: OwnerInput) -> float:

    #감정평가액이 있으면 그대로
    if owner.appraisal_value is not None:
        return owner.appraisal_value

    #토지분 : 공시가격(또는 면적 × 공시지가) × 보정률
    if owner.official_price is not None:
        land = owner.official_price * owner.appraisal_ratio * owner.exclusive_share
    elif owner.land_area_m2 is not None and owner.land_price_per_m2 is not None:
        land = (
            owner.land_area_m2 * owner.land_price_per_m2 / 10000
            * owner.appraisal_ratio * owner.exclusive_share
        )
    else:
        raise ValueError("(감정평가액), (공시가격), (토지면적, 공시지가) 중 하나는 입력해야 합니다.")

    #건물분 : 재조달원가 × 잔존율
    if (
        owner.building_area_m2
        and owner.building_elapsed_years is not None
        and owner.replacement_cost_per_m2
    ):
        building = building_value(
            BuildingSpec(
                structure=owner.building_structure or "",
                floor_area_m2=owner.building_area_m2,
                elapsed_years=owner.building_elapsed_years,
                cost_index=owner.building_cost_index,
            ),
            owner.replacement_cost_per_m2,
        ) * owner.exclusive_share
    else:
        building = 0.0

    return land + building

#면적
@dataclass 
class Areas:
    ground_m2 : float
    housing_total_m2 : float
    supply_total_m2 : float
    commercial_m2 : float

def calc_area(params: ProjectParams) -> Areas:
    ground_m2 = (params.site_area_m2 * params.floor_area_ratio) / 100

    #주택 연면적
    housing_total_m2 = ground_m2 * (1 - params.commercial_ratio)
    supply_total_m2 = housing_total_m2 * params.housing_supply_efficiency
    commercial_m2 = ground_m2 * params.commercial_ratio

    return Areas(ground_m2, housing_total_m2, supply_total_m2, commercial_m2)

#총사업비
@dataclass
class Costs:
    construction_cost_m2 : float
    underground_m2 : float
    gross_m2 : float
    construction_cost : float
    all_cost : float

#주차 1대당 지하 면적
PARKING_AREA_PER_CAR_M2 = 35.0

#세대당 지하 기타 면적
UNDERGROUND_ETC_PER_HOUSEHOLD_M2 = 11.1

#법정 주차대수
def legal_parking_count(unit_types: list[UnitType], rental_count: int, rental_exclusive_m2: float) -> float:
    homes = [(u.exclusive_area_m2, u.count) for u in unit_types] + [(rental_exclusive_m2, rental_count)]
    area_based = sum((area / 75 if area <= 85 else area / 65) * count for area, count in homes)
    minimum = sum((0.7 if area <= 60 else 1.0) * count for area, count in homes)
    return max(area_based, minimum)


def calc_construction_cost(params: ProjectParams, areas: Areas, alloc: "Allocation") -> Costs:

    #지하 연면적 = 주차대수 × 1대당 면적 + 세대수 × 기타 면적
    rental_homes = alloc.rental_count + alloc.donated_rental_count
    household_count = sum(u.count for u in alloc.unit_types) + rental_homes
    parking_count = (
        legal_parking_count(alloc.unit_types, rental_homes, params.rental_exclusive_area_m2)
        * params.parking_margin
    )
    underground_m2 = (
        parking_count * PARKING_AREA_PER_CAR_M2
        + household_count * UNDERGROUND_ETC_PER_HOUSEHOLD_M2
    )
    gross_m2 = areas.ground_m2 + underground_m2

    construction_cost_m2 = params.construction_cost_per_pyeong * PER_PYEONG_TO_PER_M2
    construction_cost = gross_m2 * construction_cost_m2

    #기타사업비
    all_cost = construction_cost * (1 + params.other_cost_ratio)
    all_cost += params.contribution_cash

    return Costs(construction_cost_m2, underground_m2, gross_m2, construction_cost, all_cost)

#세대수 배분
@dataclass
class Allocation:
    unit_types : list[UnitType]
    rental_count : int
    sale_supply_m2 : float

    #의무 임대 세대수
    base_rental_count : int = 0

    #공공기여 기부채납 공공임대 세대수
    donated_rental_count : int = 0

    #제54조 완화분 임대 세대수
    @property
    def uplift_rental_count(self) -> int:
        return self.rental_count - self.base_rental_count

def calc_allocation(params: ProjectParams, areas: Areas) -> Allocation:
    supply_total_m2 = areas.supply_total_m2

    #용적률 구간별 임대 면적
    base_share = min(params.far_base / params.floor_area_ratio, 1.0)
    base_supply_m2 = supply_total_m2 * base_share
    uplift_supply_m2 = supply_total_m2 - base_supply_m2

    base_rent_m2 = base_supply_m2 * params.base_rental_ratio
    uplift_rent_m2 = uplift_supply_m2 * params.uplift_rental_share
    rent_target_m2 = base_rent_m2 + uplift_rent_m2

    rental_count = int(rent_target_m2 / params.rental_supply_area_m2)

    #의무 임대 세대수
    base_rental_count = min(int(base_rent_m2 / params.rental_supply_area_m2), rental_count)
    rent_total_m2 = rental_count * params.rental_supply_area_m2

    #기부채납 공공임대
    donated_rental_count = params.donated_rental_count
    donated_m2 = donated_rental_count * params.rental_supply_area_m2
    sale_supply_m2 = max(supply_total_m2 - rent_total_m2 - donated_m2, 0.0)

    #평형별 세대수
    unit_types = []
    for mix in params.unit_mix_list:
        count = int(sale_supply_m2 * mix.share / mix.supply_area_m2)
        unit_types.append(UnitType(mix.name, mix.exclusive_area_m2, mix.supply_area_m2, count))

    return Allocation(
        unit_types,
        rental_count,
        sale_supply_m2,
        base_rental_count=base_rental_count,
        donated_rental_count=donated_rental_count,
    )

#종후자산 (총수입)
@dataclass
class Revenues:
    member_share : float
    blended_price_m2 : float
    housing_revenue : float
    rental_revenue : float
    commercial_revenue : float
    total_post_asset : float
    rental_building_revenue : float = 0.0
    rental_land_revenue : float = 0.0
    rental_land_area_m2 : float = 0.0
    rental_base_building_revenue : float = 0.0
    rental_uplift_building_revenue : float = 0.0
    rental_underground_m2 : float = 0.0

def calc_post_asset(params: ProjectParams, areas: Areas, alloc: Allocation, costs: Costs | None = None) -> Revenues:
    sale_count = sum(u.count for u in alloc.unit_types)
    if sale_count <= 0:
        raise ValueError("분양 세대수가 0 입니다.")

    #조합원분양가, 일반분양가 가중 평균
    member_share = min(params.member_count / sale_count, 1)

    #조합원 몫은 관리처분 시점 가격
    blended_price_m2 = params.general_price_per_m2 * (
        member_share * params.member_price_ratio * params.member_price_time_factor
        + (1 - member_share)
    )

    #세대수 기준 분양면적
    sale_supply_m2 = sum(u.count * u.supply_area_m2 for u in alloc.unit_types)
    priced_supply_m2 = sum(
        u.count * u.supply_area_m2 * size_price_index(u.exclusive_area_m2) for u in alloc.unit_types
    )

    housing_revenue = priced_supply_m2 * blended_price_m2

    #임대 인수수입 : 건물 (지상층 + 지하층)
    rental_supply_m2 = alloc.rental_count * params.rental_supply_area_m2
    base_rental_supply_m2 = alloc.base_rental_count * params.rental_supply_area_m2
    uplift_rental_supply_m2 = rental_supply_m2 - base_rental_supply_m2
    if costs is None:
        costs = calc_construction_cost(params, areas, alloc)

    #기부채납 공공임대 포함 주택 공급면적
    housing_supply_m2 = sale_supply_m2 + rental_supply_m2 + alloc.donated_rental_count * params.rental_supply_area_m2
    base_rental_underground_m2 = costs.underground_m2 * base_rental_supply_m2 / housing_supply_m2
    uplift_rental_underground_m2 = costs.underground_m2 * uplift_rental_supply_m2 / housing_supply_m2

    #고시 단가를 시점까지 보정
    band, exclusive = params.rental_floor_band, params.rental_exclusive_area_m2
    rental_base_building_revenue = (
        base_rental_supply_m2 * takeover_price_per_m2(band, exclusive)
        + base_rental_underground_m2 * takeover_basement_price_per_m2()
    ) * params.rental_cost_multiplier
    rental_uplift_building_revenue = (
        uplift_rental_supply_m2 * uplift_takeover_price_per_m2(band, exclusive)
        + uplift_rental_underground_m2 * uplift_takeover_basement_price_per_m2(band, exclusive)
    ) * params.uplift_rental_cost_multiplier
    rental_building_revenue = rental_base_building_revenue + rental_uplift_building_revenue

    #임대 인수수입 : 의무 임대 부속토지
    rental_land_area_m2 = params.site_area_m2 * base_rental_supply_m2 / housing_supply_m2
    rental_land_revenue = rental_land_area_m2 * params.rental_land_price_per_m2
    rental_revenue = rental_building_revenue + rental_land_revenue

    #상가 분양수입 = 주택 분양가 × 배수
    commercial_revenue = (
        areas.commercial_m2 * params.general_price_per_m2 * params.commercial_price_ratio
    )
    total_post_asset = housing_revenue + rental_revenue + commercial_revenue

    return Revenues(
        member_share,
        blended_price_m2,
        housing_revenue,
        rental_revenue,
        commercial_revenue,
        total_post_asset,
        rental_building_revenue=rental_building_revenue,
        rental_land_revenue=rental_land_revenue,
        rental_land_area_m2=rental_land_area_m2,
        rental_base_building_revenue=rental_base_building_revenue,
        rental_uplift_building_revenue=rental_uplift_building_revenue,
        rental_underground_m2=base_rental_underground_m2 + uplift_rental_underground_m2,
    )

#완화분 단가 이름
UPLIFT_TAKEOVER_TEXT = UPLIFT_BASIS if UPLIFT_RATIO == 1 else f"{UPLIFT_BASIS}의 {UPLIFT_RATIO:.0%}"

#사업 수지 -> 비례율
def calc_project(params: ProjectParams, alloc: Allocation) -> ProjectResult:
    warnings = []

    areas = calc_area(params)
    costs = calc_construction_cost(params, areas, alloc)
    revenues = calc_post_asset(params, areas, alloc, costs)

    #비례율 = (종후자산 − 총사업비) / 종전자산
    total_prior_asset = params.total_prior_asset
    proportional_rate = (
        (revenues.total_post_asset - costs.all_cost) / total_prior_asset * 100
    )

    sale_count = sum(u.count for u in alloc.unit_types)

    #지을 평형이 없으면 경고
    buildable = [u for u in alloc.unit_types if u.count > 0]
    if len(buildable) < len(alloc.unit_types):
        dropped = [u.name for u in alloc.unit_types if u.count <= 0]
        warnings.append(
            f"구역이 작아 {', '.join(dropped)}형은 0세대입니다. "
            "필지를 더 선택하거나 세부 설정에서 평형을 작게 바꿔 보세요."
        )

    if params.member_count > sale_count:
        warnings.append(
            f"조합원 수({params.member_count})가 분양 세대수({sale_count})보다 많습니다."
        )

    used_supply_m2 = (
        sum(u.count * u.supply_area_m2 for u in alloc.unit_types)
        + (alloc.rental_count + alloc.donated_rental_count) * params.rental_supply_area_m2
    )
    if used_supply_m2 > areas.ground_m2:
        warnings.append("공급면적 합계가 지상 연면적을 초과합니다. 세대수/평형을 확인하세요.")

    #법적상한 구간 임대 안내
    if params.floor_area_ratio > params.far_base:
        excess = params.floor_area_ratio - params.far_base
        rental_share = alloc.rental_count * params.rental_supply_area_m2 / areas.supply_total_m2
        warnings.append(
            f"정비계획 상한({params.far_base:.0f}%)을 넘는 법적상한 구간입니다. "
            f"초과 용적률 {excess:.0f}%p 의 {params.uplift_rental_share:.0%}를 국민주택규모 임대로 "
            f"공급해야 해 임대 비중이 {rental_share:.0%}까지 올라갑니다(도시정비법 제54조). "
            f"이 임대는 건물값({UPLIFT_TAKEOVER_TEXT})만 받고 부속토지는 기부채납해 "
            "늘어난 연면적만큼 수입이 늘지 않습니다."
        )

    #비례율 경고
    if proportional_rate <= 0:
        warnings.append(
            f"비례율 {proportional_rate:.1f}% : 총사업비가 종후자산을 초과해 사업이 성립하지 않습니다. "
            "공사비를 낮추거나 용적률·분양가를 높여 보세요."
        )
    elif not 60 <= proportional_rate <= 140:
        warnings.append(
            f"비례율 {proportional_rate:.1f}%는 통상 범위(80~120%)를 크게 벗어납니다. 가정값을 확인하세요."
        )
    elif not 80 <= proportional_rate <= 120:
        warnings.append(f"비례율 {proportional_rate:.1f}%는 통상 범위(80~120%)를 벗어납니다.")

    return ProjectResult(
        unit_types=alloc.unit_types,
        rental_count=alloc.rental_count,
        commercial_area_m2=areas.commercial_m2,
        gross_floor_area_m2=costs.gross_m2,
        total_cost=costs.all_cost,
        total_post_asset=revenues.total_post_asset,
        total_prior_asset=total_prior_asset,
        proportional_rate=proportional_rate,
        warnings=warnings,
    )

#목표 비례율을 맞추는 조합원분양가 비율
def solve_member_price_ratio(params: ProjectParams, alloc: Allocation, target_rate: float) -> float | None:
    rate_at_zero = calc_project(replace(params, member_price_ratio=0.0), alloc).proportional_rate
    rate_at_one = calc_project(replace(params, member_price_ratio=1.0), alloc).proportional_rate
    slope = rate_at_one - rate_at_zero
    if slope <= 1e-9:
        return None
    return (target_rate - rate_at_zero) / slope

#조합원 수 슬라이더 범위
MEMBER_COUNT_MIN_RATIO = 0.75
MEMBER_COUNT_MAX_RATIO = {ProjectType.RECONSTRUCTION: 1.0, ProjectType.REDEVELOPMENT: 1.0}

@dataclass
class MemberCountRange:
    value : int
    min : int
    max : int
    capped : bool

def member_count_range(
    params: ProjectParams, alloc: Allocation, household_count: int
) -> MemberCountRange:
    sale_count = sum(u.count for u in alloc.unit_types)
    ratio = MEMBER_COUNT_MAX_RATIO.get(params.project_type, 1.0)

    raw_max = int(household_count * ratio)
    max_count = min(raw_max, sale_count)
    min_count = min(int(household_count * MEMBER_COUNT_MIN_RATIO), max_count)

    return MemberCountRange(
        value=min(household_count, max_count),
        min=min_count,
        max=max_count,
        capped=raw_max > sale_count,
    )

#주택형별 일반분양가 지수
SIZE_PRICE_INDEX = [(39.0, 0.935), (49.0, 0.991), (59.0, 1.048), (74.0, 0.987), (84.0, 1.000), (99.0, 1.008), (114.0, 0.976)]

def size_price_index(exclusive_m2: float) -> float:
    points = SIZE_PRICE_INDEX
    if exclusive_m2 <= points[0][0]:
        return points[0][1]
    if exclusive_m2 >= points[-1][0]:
        return points[-1][1]
    for (lo_area, lo_value), (hi_area, hi_value) in zip(points, points[1:]):
        if lo_area <= exclusive_m2 <= hi_area:
            return lo_value + (hi_value - lo_value) * (exclusive_m2 - lo_area) / (hi_area - lo_area)
    return 1.0

#희망 평형 조합원분양가
def member_price(params: ProjectParams, unit_types: list[UnitType], unit_name: str) -> float:
    for unit in unit_types:
        if unit.name == unit_name:

            #관리처분 시점, 주택형 지수 반영
            return (
                unit.supply_area_m2
                * params.general_price_per_m2
                * size_price_index(unit.exclusive_area_m2)
                * params.member_price_ratio
                * params.member_price_time_factor
            )
    raise ValueError(f"존재하지 않는 평형입니다: {unit_name}")

#희망 평형 선택 목록
@dataclass
class UnitOption:
    name : str
    supply_area_m2 : float
    count : int
    member_price : float

def unit_options(params: ProjectParams, alloc: Allocation) -> list[UnitOption]:
    options = []
    for unit in alloc.unit_types:
        price = member_price(params, alloc.unit_types, unit.name)
        options.append(UnitOption(unit.name, unit.supply_area_m2, unit.count, price))

    return options

#전용면적 -> 공급면적
EXCLUSIVE_RATIO_POINTS = [
    (40.0, 0.661),
    (46.0, 0.671),
    (59.0, 59.0 / 83.4),
    (84.0, 84.0 / 112.6),
    (114.0, 114.0 / 153.9),
]

#임대 전용률
RENTAL_EXCLUSIVE_RATIO = 39.0 / 59.0

def exclusive_ratio(exclusive_m2: float) -> float:
    points = EXCLUSIVE_RATIO_POINTS
    if exclusive_m2 <= points[0][0]:
        return points[0][1]
    if exclusive_m2 >= points[-1][0]:
        return points[-1][1]

    for (lo_area, lo_ratio), (hi_area, hi_ratio) in zip(points, points[1:]):
        if lo_area <= exclusive_m2 <= hi_area:
            t = (exclusive_m2 - lo_area) / (hi_area - lo_area)
            return lo_ratio + (hi_ratio - lo_ratio) * t

    return points[-1][1]

def supply_area_from_exclusive(exclusive_m2: float) -> float:
    if exclusive_m2 <= 0:
        raise ValueError("전용면적은 0보다 커야 합니다")
    return exclusive_m2 / exclusive_ratio(exclusive_m2)

#임대 전용면적 -> 공급면적
def rental_supply_from_exclusive(exclusive_m2: float) -> float:
    if exclusive_m2 <= 0:
        raise ValueError("임대 전용면적은 0보다 커야 합니다")
    small_area, small_ratio = 39.0, RENTAL_EXCLUSIVE_RATIO
    sale_area = EXCLUSIVE_RATIO_POINTS[0][0]
    if exclusive_m2 >= sale_area:
        ratio = exclusive_ratio(exclusive_m2)
    elif exclusive_m2 <= small_area:
        ratio = small_ratio
    else:
        t = (exclusive_m2 - small_area) / (sale_area - small_area)
        ratio = small_ratio + (exclusive_ratio(sale_area) - small_ratio) * t
    return exclusive_m2 / ratio

#세대수 비율 -> UnitMix 면적 몫
MAX_UNIT_TYPES = 4

def unit_mix_from_household_ratio(entries: list[tuple[float, float]]) -> list[UnitMix]:
    if not entries:
        raise ValueError("평형을 최소 1개 입력해야 합니다")
    if len(entries) > MAX_UNIT_TYPES:
        raise ValueError(f"평형은 최대 {MAX_UNIT_TYPES}개까지 입력할 수 있습니다")

    areas = [(float(ex), supply_area_from_exclusive(float(ex))) for ex, _ in entries]
    ratios = [max(float(r), 0.0) for _, r in entries]

    #비율 정규화
    weights = [r * supply for r, (_, supply) in zip(ratios, areas)]
    weight_total = sum(weights)
    if weight_total <= 0:
        raise ValueError("평형 비율의 합이 0보다 커야 합니다")

    mixes = []
    for (exclusive, supply), weight in zip(areas, weights):
        if weight <= 0:
            continue
        name = f"{exclusive:g}"
        mixes.append(
            UnitMix(
                name=name,
                exclusive_area_m2=round(exclusive, 2),
                supply_area_m2=round(supply, 2),
                share=weight / weight_total,
            )
        )

    if not mixes:
        raise ValueError("비율이 0보다 큰 평형이 최소 1개 있어야 합니다")

    return mixes

#평형별 분담금
@dataclass
class UnitContribution:
    name : str
    exclusive_area_m2 : float
    supply_area_m2 : float
    count : int
    member_price : float
    contribution : float
    contribution_ratio : float

def calc_contribution_all(
    params: ProjectParams, alloc: Allocation, owner: OwnerInput
) -> list[UnitContribution]:
    project = calc_project(params, alloc)
    right_value = estimate_prior_asset(owner) * project.proportional_rate / 100

    units = []
    for unit in alloc.unit_types:

        #0세대 평형 제외
        if unit.count <= 0:
            continue

        price = member_price(params, alloc.unit_types, unit.name)
        contribution = price - right_value
        units.append(
            UnitContribution(
                name=unit.name,
                exclusive_area_m2=unit.exclusive_area_m2,
                supply_area_m2=unit.supply_area_m2,
                count=unit.count,
                member_price=price,
                contribution=contribution,
                contribution_ratio=contribution / price if price else 0.0,
            )
        )

    return units

#조합원 개인 분담금
def calc_contribution(params: ProjectParams, alloc: Allocation, owner: OwnerInput) -> ContributionResult:
    project = calc_project(params, alloc)
    prior_asset = estimate_prior_asset(owner)
    right_value = prior_asset * project.proportional_rate / 100
    price = member_price(params, alloc.unit_types, owner.desired_unit)

    return ContributionResult(
        prior_asset=prior_asset,
        proportional_rate=project.proportional_rate,
        right_value=right_value,
        member_price=price,
        contribution=price - right_value,
        project=project,
    )