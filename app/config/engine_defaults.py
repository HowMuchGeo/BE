from app.services.engine import policy

#Engine 파일 기본값
ENGINE_DEFAULTS = {
    "parking_margin": 1.29,
    "housing_supply_efficiency": 1.0,
    "commercial_price_ratio": 0.7,
    "base_rental_ratio": 0.10,
    "uplift_rental_share": float(policy.RULES["redev_uplift_rental_share"]),
    "rental_supply_area_m2": 59.0,
    "rental_floor_band": "16~25층",
    "rental_exclusive_area_m2": 39.0,
    "appraisal_ratio": 1.527,
}

#ProjectParams 전용 제외 대상을 뺀 기본값
ENGINE_DEFAULTS_FOR_PARAMS = {
    "housing_supply_efficiency": 1.0,
    "commercial_price_ratio": 0.7,
    "base_rental_ratio": 0.10,
    "uplift_rental_share": float(policy.RULES["redev_uplift_rental_share"]),
    "rental_supply_area_m2": 59.0,
    "rental_exclusive_area_m2": 39.0,
}

#UnitMix 사전 기본값
UNIT_MIX = [
    {"name": "59", "exclusive_area_m2": 59.0, "supply_area_m2": 83.4, "share": 0.381},
    {"name": "84", "exclusive_area_m2": 84.0, "supply_area_m2": 112.6, "share": 0.563},
    {"name": "114", "exclusive_area_m2": 114.0, "supply_area_m2": 153.9, "share": 0.056},
]

#조합원 수 슬라이더 범위 계수
MEMBER_COUNT_MIN_RATIO = 0.75
MEMBER_COUNT_MAX_RATIO = {"재건축": 1.0, "재개발": 1.0}
MEMBER_COUNT_UNKNOWN_MIN_RATIO = 0.3

#사업 기간
DEFAULT_PROJECT_PERIOD_YEARS = 13
DEFAULT_COMPLETION_YEARS = 21
PROJECT_PERIOD_TICKS = [11, 13, 16, 18]
PROJECT_PERIOD_TRACK = (10, 30)

#고시일
COMPLETION_GAP_YEARS = 8
COMPLETION_GAP_MIN = 3

#착공 → 준공 (년)
CONSTRUCTION_YEARS = 3.05

#지가 연평균 상승률
LAND_PRICE_ANNUAL_RATE = 0.0445

#종전자산 평가 기준시점이 관리처분보다 얼마나 앞서는가
PRIOR_ASSET_LEAD_YEARS = 3.3

#주차 여유율
PARKING_MARGIN_DEFAULT = 1.29
PARKING_MARGIN_MIN = 1.0
PARKING_MARGIN_MAX = 1.59

#기타사업비 비율
OTHER_COST_RATIO_BY_TYPE = {"재개발": 0.707, "재건축": 0.474}
OTHER_COST_RATIO_DEFAULT = OTHER_COST_RATIO_BY_TYPE["재개발"]
OTHER_COST_RATIO_PLAN_STAGE = 0.360
OTHER_COST_RATIO_MIN = 0.19
OTHER_COST_RATIO_MAX = 0.75

#조합원분양가 비율
MEMBER_PRICE_TARGET_RATE = 100.0
MEMBER_PRICE_RATIO_MIN = 0.0
MEMBER_PRICE_RATIO_MAX = 1.0

#재건축
RECON_BASE_RENTAL_RATIO = 0.0
RECON_UPLIFT_RENTAL_SHARE = float(policy.RULES["recon_uplift_rental_share"])
APT_PRICE_REALIZATION_RATE = 0.69
HOUSE_PRICE_REALIZATION_RATE = 0.536
HOUSING_REALIZATION_RATES = {"공동": APT_PRICE_REALIZATION_RATE, "단독": HOUSE_PRICE_REALIZATION_RATE}