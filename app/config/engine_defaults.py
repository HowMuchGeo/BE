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

#재개발 의무 임대 (연면적 기준은 ENGINE_DEFAULTS 의 base_rental_ratio 0.10)
#  서울특별시고시 제2022-493호 (2022-12-12) : 세대수의 15% 또는 연면적의 10% 중 하나 (상업지역은 5% 또는 5%)
#    — 서울시의회 주택공간위원회 검토보고서(2024-02-27)의 인용으로 확인
REDEV_RENTAL_HOUSEHOLD_RATIO = 0.15
#  국토교통부고시 제2024-26호 제4조④1 : 건설하는 주택 전체 세대수가 200세대 미만이면 임대주택을 짓지 않을 수 있다
REDEV_RENTAL_EXEMPT_BELOW_HOUSEHOLDS = 200
#  같은 항 제5호 : 제1종 일반주거지역에서 용도지역을 변경하지 않고 개발계획을 수립하는 경우에도 짓지 않을 수 있다
REDEV_RENTAL_EXEMPT_ZONING = "제1종일반주거지역"
#  판정하지 않는 것 (입력에 정보가 없다) : 같은 항 제2~4호 — 자연경관지구 · 최고고도지구 · 경관 보호 · 한옥 보존 ·
#    항공법 · 군사시설 고도 제한으로 7층 이하가 되는 경우 / 같은 조 ⑤ — 학교용지 확보 시 임대 세대수 50% 범위 차감
#    (2026-11-26 결정 : 변수가 많아 넣지 않고 안내문에 적는다)

#조합원 수 슬라이더 범위 계수
MEMBER_COUNT_MIN_RATIO = 0.75
MEMBER_COUNT_MAX_RATIO = {"재건축": 1.0, "재개발": 1.0}
MEMBER_COUNT_UNKNOWN_MIN_RATIO = 0.3

#사업 기간 (재개발, 오늘 = 정비구역 지정일부터 몇 년 뒤인가) — 2026-11-24 다시 잼
#  자료 : 서울시 정비사업 추진현황 (열린데이터광장 OA-22856, 2026-06 말), 주택정비형 재개발 164건
#  예전 값(고시일 13 · 최종 인가 21)은 관리처분에 도달한 40건의 단순 중앙값 13.4년이었다.
#    이 통계에는 준공한 구역이 없다 (추진 단계가 구역지정 ~ 착공뿐). 40건은 모두 2000~2014년에 지정돼
#    2017~2026년에 관리처분을 받은 구역이라, 빨리 끝난 구역이 빠진 채 오래 걸린 구역만 잰 값이었다
#  지금 값 : 최근에 그 단계를 지난 구역만, 아직 도달하지 못한 구역은 기준일까지 지난 기간으로 넣어 보정한 중앙값
#    (Kaplan-Meier, 25% · 중앙 · 75%)
#    구역지정 → 조합설립 : 2020년 이후 지정 88건 (도달 43)       0.5 · 1.3 · 1.7 년
#    조합설립 → 관리처분 : 2015~2019년 조합설립 25건 (도달 15)   7.0 · 7.7 · 9.4 년
#    관리처분 → 착공     : 2020년 이후 관리처분 33건 (도달 11)   3.6 · 3.9 · 4.4 년
#    착공 → 준공         : 3.05 년 (CONSTRUCTION_YEARS)
#    ∴ 고시일 = 1.3 + 7.7 = 9.0 → 9 / 최종 인가 = 9.0 + 3.9 + 3.05 = 15.95 → 16
#  대조 : 서울시 「주택공급 촉진방안」(2025-07-24) 의 평균 실적 — 추진위·조합설립 3.5년 + 사업시행·관리처분·이주 8.5년
#         → 구역지정 → 착공 12.0년 (위 값은 12.9년), 전체 18.5년에서 구역지정 전 2.5년을 빼면 구역지정 → 준공 16.0년
#         https://mediahub.seoul.go.kr/archives/2015170
#  측정 스크립트 : ~/Howmuch_GEO/참고자료/period_km.py (저장소 밖)
DEFAULT_PROJECT_PERIOD_YEARS = 9            # 분담금 고시일 (관리처분인가)
DEFAULT_COMPLETION_YEARS = 16               # 최종 인가 (준공)
#기준점 : 빠른 쪽 1.3 + 7.0 = 8.3 · 중앙 9.0 · 느린 쪽 1.3 + 9.4 = 10.7 · 과거 실적 13.4 (2000~2014년 지정 40건)
PROJECT_PERIOD_TICKS = [8, 9, 11, 13]
#트랙 : 관리처분에 도달한 40건의 최단 7.2년 ~ 최장 21.7년 + 기본 간격
PROJECT_PERIOD_TRACK = (7, 30)

#고시일 → 최종 인가 간격 (년). 기본 7 = 관리처분 → 착공 3.9 + 공사 3.05 = 6.95 / 최소 3 = 공사 기간
COMPLETION_GAP_YEARS = 7
COMPLETION_GAP_MIN = 3

#진행 단계 (재개발) — 지금 사업이 어느 단계인지 고르면 사업 기간 기본값이 "그 단계 → 관리처분" 남은 기간으로 바뀐다
#  period = [분담금 고시일, 최종 인가] (오늘부터 년), track = 슬라이더 범위. 통계 · 방법은 위 사업 기간과 같다
#    정비구역 지정 : 9.0 (위와 같다) → [9, 16]
#    조합설립인가  : 조합설립 → 관리처분 7.7 → 8, 준공 7.7 + 6.95 = 14.65 → 15
#    사업시행인가  : 사업시행 → 관리처분 3.1 (2015년 이후 사업시행 46건, 도달 31) → 3, 준공 3.1 + 6.95 = 10.05 → 10
#    관리처분인가  : 0, 준공 = 관리처분 → 착공 3.9 + 공사 3.05 = 6.95 → 7
#    착공          : 0, 준공 = 공사 3.05 → 3
#  종전자산 평가 시점(고시일 − 3.3년)은 0 아래로 내려가지 않으므로, 사업시행인가 이후 단계는 오늘 기준으로 평가된다
PROJECT_STAGES = [
    {"key": "designation", "label": "정비구역 지정", "period": [9, 16], "track": [7, 30]},
    {"key": "union", "label": "조합설립인가", "period": [8, 15], "track": [0, 30]},
    {"key": "implementation", "label": "사업시행인가", "period": [3, 10], "track": [0, 30]},
    {"key": "management", "label": "관리처분인가", "period": [0, 7], "track": [0, 30]},
    {"key": "construction", "label": "착공", "period": [0, 3], "track": [0, 30]},
]
PROJECT_STAGE_DEFAULT = "designation"
PROJECT_STAGE_KEYS = [s["key"] for s in PROJECT_STAGES]

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