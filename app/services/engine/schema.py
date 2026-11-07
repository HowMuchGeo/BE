from dataclasses import dataclass, field
from app.services.engine.rental_cost import DEFAULT_FLOOR_BAND
from enum import Enum

PER_PYEONG_TO_PER_M2 = 1 / 3.3058

class ProjectType(str, Enum):
    REDEVELOPMENT = "재개발"
    RECONSTRUCTION = "재건축"

#분양 평형
@dataclass
class UnitType:
    name : str
    exclusive_area_m2 : float
    supply_area_m2 : float
    count : int

#공급 평형 구성
@dataclass
class UnitMix:
    name : str
    exclusive_area_m2 : float
    supply_area_m2 : float
    share : float

#정비구역 단위 파라미터
@dataclass(kw_only=True)
class ProjectParams:
    name : str
    project_type : ProjectType

    #규모
    site_area_m2 : float
    floor_area_ratio: float

    #주차 여유율
    parking_margin: float

    #분양
    member_count: int
    general_price_per_m2 : float
    member_price_ratio : float

    #조합원분양가 시점 계수
    member_price_time_factor : float = 1.0

    #임대 인수수입
    rental_floor_band : str = DEFAULT_FLOOR_BAND

    rental_exclusive_area_m2 : float = 39.0

    #의무 임대 인수가격 시점 보정 배수
    rental_cost_multiplier : float = 1.0

    #제54조 완화분 인수가격 시점 보정 배수
    uplift_rental_cost_multiplier : float = 1.0

    #의무 임대 부속토지 감정가
    rental_land_price_per_m2 : float = 0.0

    #공공기여 현금 기부채납액
    contribution_cash : float = 0.0

    #기부채납 공공임대 세대수
    donated_rental_count : int = 0

    #비용
    construction_cost_per_pyeong : float
    other_cost_ratio : float

    #종전자산 총액
    total_prior_asset : float | None = None


    #정비계획 상한용적률
    far_base : float
    base_rental_ratio : float
    uplift_rental_share : float
    rental_supply_area_m2 : float

    #상가
    commercial_ratio : float

    #주택 연면적 → 공급면적 전환율
    housing_supply_efficiency : float
    commercial_price_ratio : float

    #평형 구성
    unit_mix_list : list[UnitMix]

    #생성 시점 검증
    def __post_init__(self):
        #평형 구성 필수
        if not self.unit_mix_list:
            raise ValueError("unit_mix_list 가 비어 있습니다.")

        #share 합계 1.0
        share_sum = sum(u.share for u in self.unit_mix_list)
        if abs(share_sum - 1.0) > 1e-6:
            raise ValueError(f"unit_mix_list 의 share 합계가 1.0 이 아닙니다: {share_sum}")

        #비율 범위
        if not 0 <= self.base_rental_ratio <= 1:
            raise ValueError(f"base_rental_ratio 는 0~1 이어야 합니다: {self.base_rental_ratio}")
        if not 0 <= self.uplift_rental_share <= 0.75:
            raise ValueError(
                f"uplift_rental_share 는 0~0.75 이어야 합니다(법정 상한 75%): {self.uplift_rental_share}"
            )
        if self.far_base <= 0:
            raise ValueError(f"far_base 는 0보다 커야 합니다: {self.far_base}")
        if not 0 < self.housing_supply_efficiency <= 1:
            raise ValueError(
                f"housing_supply_efficiency 는 0~1 이어야 합니다: {self.housing_supply_efficiency}"
            )
        if self.rental_land_price_per_m2 < 0:
            raise ValueError(
                f"rental_land_price_per_m2 는 0 이상이어야 합니다: {self.rental_land_price_per_m2}"
            )
        if self.contribution_cash < 0 or self.donated_rental_count < 0:
            raise ValueError(
                f"공공기여 현금·기부 세대수는 0 이상이어야 합니다: {self.contribution_cash}, {self.donated_rental_count}"
            )

        #비주거 비율 검사
        non_housing = self.commercial_ratio
        if not 0 <= non_housing < 1:
            raise ValueError(
                f"commercial_ratio 는 0 이상 1 미만이어야 합니다: {non_housing}"
            )

        #종전자산 총액 필수
        if self.total_prior_asset is None or self.total_prior_asset <= 0:
            raise ValueError(
                "total_prior_asset(종전자산 총액)이 필요합니다. 선택 구역의 공시지가 총액에서 환산하세요."
            )

#필지 입력값
@dataclass
class ParcelInfo:
    pnu : str
    area_m2 : float
    land_price_per_m2 : float
    zoning : str
    land_category : str | None = None

#구역 요약
@dataclass
class ZoneSummary:
    site_area_m2 : float
    far_min : float
    far_max : float
    land_value_total : float
    pnus : list[str]
    warnings : list[str] = field(default_factory=list)
    region : str | None = None
    zoning : str | None = None

#조합원 개인 입력값
@dataclass(kw_only=True)
class OwnerInput:
    desired_unit: str
 
    #종전자산 입력
    appraisal_value: float | None = None
    official_price: float | None = None
    land_area_m2: float | None = None
    land_price_per_m2: float | None = None

    #건물 정보
    building_structure: str | None = None
    building_area_m2: float | None = None
    building_elapsed_years: float | None = None
    exclusive_share: float = 1.0
    replacement_cost_per_m2: float | None = None
    building_cost_index: float = 1.0

    #공시가격 -> 종전자산 환산 배수
    appraisal_ratio: float

#사업 단위 계산 결과
@dataclass
class ProjectResult:
    unit_types : list[UnitType]
    rental_count : int
    commercial_area_m2: float
    gross_floor_area_m2 : float
    total_cost : float
    total_post_asset : float
    total_prior_asset : float
    proportional_rate : float
    warnings : list[str] = field(default_factory=list)
    
#조합원 개인 계산 결과
@dataclass
class ContributionResult:
    prior_asset : float
    proportional_rate : float
    right_value : float
    member_price : float
    contribution : float
    project : ProjectResult