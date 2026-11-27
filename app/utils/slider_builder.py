from app.services.engine.rental_cost import DEFAULT_FLOOR_BAND, FLOOR_BANDS
from app.services.engine.schema import ZoneSummary
from app.services.engine.zone import COMMERCIAL_RATIO_MAX, DEFAULT_COMMERCIAL_RATIO_MAX, FarPlan, far_plan
from app.services.predict.construction_cost import CostPrediction
from app.services.predict.sale_price import SalePrediction
from app.config.recon_defaults import (
    RECON_COMPLETION_GAP_YEARS,
    RECON_DEFAULT_COMPLETION_YEARS,
    RECON_DEFAULT_PROJECT_PERIOD_YEARS,
    RECON_PROJECT_PERIOD_TICKS,
    RECON_PROJECT_PERIOD_TRACK,
    RECON_PROJECT_STAGES,
)
from app.config.engine_defaults import (
    PARKING_MARGIN_DEFAULT,
    PARKING_MARGIN_MAX,
    PARKING_MARGIN_MIN,
    COMPLETION_GAP_MIN,
    COMPLETION_GAP_YEARS,
    DEFAULT_COMPLETION_YEARS,
    DEFAULT_PROJECT_PERIOD_YEARS,
    MEMBER_COUNT_MAX_RATIO,
    MEMBER_COUNT_MIN_RATIO,
    MEMBER_PRICE_RATIO_MAX,
    MEMBER_PRICE_RATIO_MIN,
    MEMBER_PRICE_TARGET_RATE,
    OTHER_COST_RATIO_BY_TYPE,
    OTHER_COST_RATIO_DEFAULT,
    OTHER_COST_RATIO_MAX,
    OTHER_COST_RATIO_MIN,
    PROJECT_PERIOD_TICKS,
    PROJECT_PERIOD_TRACK,
    PROJECT_STAGE_DEFAULT,
    PROJECT_STAGES,
)

#용적률 노드 슬라이더
#  종상향이면 far_plan 의 첫 노드가 "종상향 최소"(최소 공공기여만 내고 받는 용적률)다.
#  이 노드는 화면 선택지로 내보내지 않는다 — 종상향 최소 공공기여는 min_contribution 으로 계산에서만 쓴다 (2026-11-27).
#  그러면 종상향일 때 기본값은 다음 노드(상한)가 된다
def _far_slider(plan: FarPlan) -> dict:
    shown = [n for n in plan.nodes if "종상향 최소" not in n.label] or list(plan.nodes)
    nodes = [n.value for n in shown]
    default = next((n.value for n in shown if "허용" in n.label), nodes[0])
    return {
        "value": default,
        "options": nodes,
        "min": nodes[0],
        "max": nodes[-1],
        "tiers": [n.label for n in shown],
        "contributions": [round(n.contribution, 4) for n in shown],
        "min_contribution": round(plan.min_contribution, 4),
    }


#사업 기간 슬라이더
#  재건축은 재건축 실측값(recon_defaults — 구역지정 → 관리처분 6.5년)으로 만든다. 재개발 값은 그대로다
def _period_slider(project_type: str = "재개발") -> dict:
    if project_type == "재건축":
        track_min, track_max = RECON_PROJECT_PERIOD_TRACK
        value = [RECON_DEFAULT_PROJECT_PERIOD_YEARS, RECON_DEFAULT_COMPLETION_YEARS]
        ticks, gap = RECON_PROJECT_PERIOD_TICKS, RECON_COMPLETION_GAP_YEARS
    else:
        track_min, track_max = PROJECT_PERIOD_TRACK
        value = [DEFAULT_PROJECT_PERIOD_YEARS, DEFAULT_COMPLETION_YEARS]
        ticks, gap = PROJECT_PERIOD_TICKS, COMPLETION_GAP_YEARS
    stages = RECON_PROJECT_STAGES if project_type == "재건축" else PROJECT_STAGES
    return {
        "value": value,
        "min": track_min,
        "max": track_max,
        "step": 1,
        "range": True,
        "ticks": ticks,
        "gap": {"value": gap, "min": COMPLETION_GAP_MIN, "max": track_max - track_min},
        "labels": ["분담금 고시일", "최종 인가"],
        #진행 단계 : 고르면 value · min · max 를 그 단계의 period · track 으로 바꾸고, 계산 요청에 project_stage 로 보낸다
        "stage": PROJECT_STAGE_DEFAULT,
        "stages": [dict(s) for s in stages],
    }


#슬라이드 변수 초기 설정값
def build_sliders(
    zone: ZoneSummary,
    cost: CostPrediction,
    household_count: int | None = None,
    project_type: str = "재개발",
    sale: SalePrediction | None = None,
    measured_member_count: int | None = None,
    far: FarPlan | None = None,
) -> dict:
    commercial_max = COMMERCIAL_RATIO_MAX.get(zone.zoning or "", DEFAULT_COMMERCIAL_RATIO_MAX)

    sliders = {
        #용적률
        "floor_area_ratio": _far_slider(far or far_plan(zone.zoning, zone.far_min, zone.far_max)),

        #조합원 분양가 비율
        #  자동(auto)이면 관리처분 비례율이 target_rate 가 되도록 비율을 역산한다 → 비율 슬라이더는 잠그고 「비례율만 재조정」 칸을 연다.
        #  자동을 끄면 사용자가 비율을 정하고 비례율은 계산 결과가 된다 → 「비례율만 재조정」 칸을 잠근다.
        #  locks 는 그 상태에서 입력을 막을 칸이다 (member_price_target_rate 는 계산 요청의 같은 이름 값)
        "member_price_ratio": {
            "value": None, "min": MEMBER_PRICE_RATIO_MIN, "max": MEMBER_PRICE_RATIO_MAX, "auto": True,
            "target_rate": {"value": MEMBER_PRICE_TARGET_RATE, "min": 50, "max": 200},
            "locks": {"auto": ["member_price_ratio"], "manual": ["member_price_target_rate"]},
        },

        #기타사업비 비율
        "other_cost_ratio": {
            "value": OTHER_COST_RATIO_BY_TYPE.get(project_type, OTHER_COST_RATIO_DEFAULT),
            "min": OTHER_COST_RATIO_MIN,
            "max": OTHER_COST_RATIO_MAX,
        },

        #주차 여유율
        "parking_margin": {"value": PARKING_MARGIN_DEFAULT, "min": PARKING_MARGIN_MIN, "max": PARKING_MARGIN_MAX},

        #상가 비율
        "commercial_ratio": {
            "value": min(0.02, commercial_max),
            "min": 0.0,
            "max": commercial_max,
        },

        "construction_cost_per_pyeong": {
            "value": cost.cost_per_pyeong,
            "min": cost.slider_min,
            "max": cost.slider_max,
        },

        "general_price_per_m2": (
            {"value": sale.price_per_m2, "min": sale.slider_min, "max": sale.slider_max}
            if sale
            else {"value": 998.25, "min": 700, "max": 1300}
        ),

        #임대 인수수입
        "rental_floor_band": {"value": DEFAULT_FLOOR_BAND, "options": FLOOR_BANDS},

        #사업 기간
        "project_period_years": _period_slider(project_type),
    }

    if household_count:
        max_ratio = MEMBER_COUNT_MAX_RATIO.get(project_type, 1.0)
        sliders["member_count"] = {
            "value": household_count,
            "min": int(household_count * MEMBER_COUNT_MIN_RATIO),
            "max": int(household_count * max_ratio),
        }

    #건축물대장으로 실측한 조합원 수가 있으면 그것을 기본값으로 사용
    if measured_member_count:
        existing = sliders.get("member_count")
        low = int(measured_member_count * MEMBER_COUNT_MIN_RATIO)
        high = int(measured_member_count * MEMBER_COUNT_MAX_RATIO.get(project_type, 1.0))
        if existing:

            #세대수 기준 범위와 실측값을 모두 담을 수 있게 넓힘
            low = min(low, existing["min"])
            high = max(high, existing["max"])

        #기본값 = 최댓값
        high = max(high, measured_member_count)
        sliders["member_count"] = {
            "value": high,
            "min": max(1, low),
            "max": high,
            "measured": True,
        }

    #최후 폴백
    if "member_count" not in sliders:
        parcel_count = max(len(zone.pnus or []), 1)
        sliders["member_count"] = {
            "value": parcel_count,
            "min": parcel_count,
            "max": parcel_count * 10,
            "assumed": True,
        }

    return sliders