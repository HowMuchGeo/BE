from app.config.engine_defaults import (
    COMPLETION_GAP_MIN,
    COMPLETION_GAP_YEARS,
    CONSTRUCTION_YEARS,
    DEFAULT_COMPLETION_YEARS,
    DEFAULT_PROJECT_PERIOD_YEARS,
    ENGINE_DEFAULTS,
    ENGINE_DEFAULTS_FOR_PARAMS,
    LAND_PRICE_ANNUAL_RATE,
    MEMBER_COUNT_UNKNOWN_MIN_RATIO,
    MEMBER_PRICE_RATIO_MAX,
    MEMBER_PRICE_RATIO_MIN,
    MEMBER_PRICE_TARGET_RATE,
    APT_PRICE_REALIZATION_RATE,
    HOUSING_REALIZATION_RATES,
    RECON_BASE_RENTAL_RATIO,
    RECON_UPLIFT_RENTAL_SHARE,
    PRIOR_ASSET_LEAD_YEARS,
    UNIT_MIX,
)
from app.schemas.realestate.realestate_request import ContributionRequest
from app.schemas.realestate.realestate_response import ContributionResponse
from app.services.credit_service import consume_credit_token
from app.services.organization_service import get_active_organization
from app.database.database_connection import get_db
from sqlalchemy.orm import Session
from app.services.land_service import fetch_exclusive_total_cached
from app.exceptions.exceptions_handler import BadRequestException, ServiceUnavailableException, UnauthorizedException
from app.services.engine.calc import (
    MemberCountRange,
    calc_allocation,
    calc_area,
    calc_construction_cost,
    calc_contribution_all,
    calc_post_asset,
    estimate_prior_asset,
    calc_project,
    member_count_range,
    rental_supply_from_exclusive,
    solve_member_price_ratio,
    unit_mix_from_household_ratio,
    unit_options,
)
from app.services.engine.prior_asset import BuildingSpec, ParcelValuation, aggregate_zone, estimate
from app.services.engine.public_contribution import (
    CASH_MAX_SHARE,
    PRESETS as CONTRIBUTION_PRESETS,
    SITE_VALUE_WEIGHT,
    contribution_need,
    mixed_contribution,
    normalize_mix,
)
from app.services.engine.rental_cost import (
    ANNOUNCED as RENTAL_ANNOUNCED,
    ANNOUNCED_YM as RENTAL_ANNOUNCED_YM,
    BASEMENT_BUILD_COST_KRW_THOUSAND,
    NOTICE as RENTAL_NOTICE,
    STANDARD_ANNUAL_RATE,
    SURCHARGE_RATIO as RENTAL_SURCHARGE_RATIO,
    TAKEOVER_RATIO as RENTAL_TAKEOVER_RATIO,
    UPLIFT_ANNOUNCED_YM,
    UPLIFT_BASIS,
    UPLIFT_NOTICE,
    UPLIFT_RATIO,
    base_build_cost_per_m2,
    takeover_price_per_m2,
    uplift_takeover_price_per_m2,
)
from app.services.engine.schema import PER_PYEONG_TO_PER_M2, ProjectType, UnitMix, OwnerInput, ProjectParams
from app.services.engine.zone import SIGUNGU_NAME, sigungu_code
from app.services.predict import trend
from app.services.predict.construction_cost import contract_excess_rate, load_index
from app.services.predict.sale_price import escalate as escalate_sale
from dataclasses import asdict
from datetime import datetime
from fastapi import APIRouter, Depends, Request
import logging
import math

#부동산 계산식 API 라우터 설정
router = APIRouter(
    prefix="/api/v1",
    tags=["Contribution Router"]
)

#백엔드 Logger
logger = logging.getLogger(__name__)

#"YYYY-MM" 을 연 단위(소수 허용)로 이동
def _shift_ym(ym: str, years: float) -> str:
    months = int(ym[:4]) * 12 + (int(ym[5:7]) - 1) + round(years * 12)
    return f"{months // 12:04d}-{months % 12 + 1:02d}"

#사업 기간 슬라이더 값
def _parse_period(value) -> tuple[float, float]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        mgmt, complete = float(value[0]), float(value[1])
    elif value is not None and not isinstance(value, (list, tuple, dict)):
        mgmt = float(value)
        complete = mgmt + COMPLETION_GAP_YEARS
    else:
        mgmt, complete = float(DEFAULT_PROJECT_PERIOD_YEARS), float(DEFAULT_COMPLETION_YEARS)
    mgmt = max(mgmt, 0.0)
    #최종 인가는 고시일 + 최소 간격(공사기간 중앙값) 이상. 최대 제한은 없다 (슬라이더와 같은 규칙)
    complete = max(complete, mgmt + COMPLETION_GAP_MIN)
    return mgmt, complete

#구역의 시군구
def _region(valuations) -> str | None:
    area_by_code: dict[str, float] = {}
    for v in valuations or []:
        code = sigungu_code(v.pnu)
        if code:
            area_by_code[code] = area_by_code.get(code, 0.0) + v.land_area_m2
    if not area_by_code:
        return None
    return SIGUNGU_NAME.get(max(area_by_code, key=area_by_code.get))

#슬라이더 및 추가 변수들로 계산하는 API
@router.post(
    "/contribution",
    response_model = ContributionResponse,
    summary = "조합원 개인 분담금 및 사업성 계산")
async def get_contribution(req: ContributionRequest, request: Request, session: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if user_id is None:
        raise UnauthorizedException("로그인이 필요합니다.")

    try:
        sliders = dict(req.sliders)

        #감정평가 보정률은 ProjectParams 의 필드가 아니라 종전자산 환산에만 사용
        appraisal_ratio = sliders.pop("appraisal_ratio", ENGINE_DEFAULTS["appraisal_ratio"])

        #예전 요청의 세대당 주차대수는 여유율로 바꿀 수 없음
        if "parking_per_household" in sliders:
            sliders.pop("parking_per_household")
            sliders.setdefault("parking_margin", ENGINE_DEFAULTS["parking_margin"])

        #조합원분양가 비율
        member_price_auto = sliders.get("member_price_ratio") is None
        if member_price_auto:

            #역산 전 임시값
            sliders["member_price_ratio"] = MEMBER_PRICE_RATIO_MAX

        #사업 유형
        project_type = (
            ProjectType.RECONSTRUCTION if req.project_type == ProjectType.RECONSTRUCTION.value else ProjectType.REDEVELOPMENT
        )
        recon = req.reconstruction if project_type == ProjectType.RECONSTRUCTION else None

        #사업 기간
        mgmt_years, complete_years = _parse_period(sliders.pop("project_period_years", None))

        #항목별 시점
        now_ym = datetime.now().strftime("%Y-%m")
        start_years = max(complete_years - CONSTRUCTION_YEARS, mgmt_years)
        mgmt_ym = _shift_ym(now_ym, mgmt_years)
        start_ym = _shift_ym(now_ym, start_years)
        complete_ym = _shift_ym(now_ym, complete_years)
        base_cost_per_pyeong = sliders.get("construction_cost_per_pyeong", 0.0)
        base_sale_per_m2 = sliders["general_price_per_m2"]
        cost_index = load_index()
        cost_rate = trend.estimate_annual_rate(cost_index).annual_rate
        cost_now = trend.index_at(now_ym, cost_index, cost_rate)

        def cost_at(ym: str) -> float:
            return base_cost_per_pyeong * trend.index_at(ym, cost_index, cost_rate) / cost_now

        #임대 건물 인수가격 시점 보정 배수
        rental_index_announced = trend.index_at(RENTAL_ANNOUNCED_YM, cost_index, cost_rate)

        def rental_multiplier(ym: str) -> float:
            return trend.index_at(ym, cost_index, cost_rate) / rental_index_announced

        #제54조 완화분 건물 인수가격 시점 보정 배수
        uplift_index_announced = trend.index_at(UPLIFT_ANNOUNCED_YM, cost_index, cost_rate)

        def uplift_multiplier(ym: str) -> float:
            if UPLIFT_BASIS == "기본형건축비":
                return trend.index_at(ym, cost_index, cost_rate) / uplift_index_announced
            months = (int(ym[:4]) * 12 + int(ym[5:7])) - (int(UPLIFT_ANNOUNCED_YM[:4]) * 12 + int(UPLIFT_ANNOUNCED_YM[5:7]))
            return (1 + STANDARD_ANNUAL_RATE) ** (months / 12)

        #공사비 도급단가 (고시일)
        cost_contract = cost_at(mgmt_ym)

        #확정 이후 증액 = 기성(착공~준공) 기간 평균 배수
        excess = contract_excess_rate(_region(req.parcel_valuations))
        escalation_index = trend.average_index_ratio(start_ym, complete_ym, mgmt_ym, cost_index, cost_rate)
        escalation_total = trend.average_index_ratio(
            start_ym, complete_ym, mgmt_ym, cost_index, cost_rate, excess_rate=excess.rate
        )

        #분양가
        sale_mgmt = escalate_sale(base_sale_per_m2, now_ym, mgmt_ym)
        sale_start = escalate_sale(base_sale_per_m2, now_ym, start_ym)

        #종전자산도 미래 시점으로 밈
        prior_years = max(mgmt_years - PRIOR_ASSET_LEAD_YEARS, 0.0)
        prior_growth = (1 + LAND_PRICE_ANNUAL_RATE) ** prior_years

        if not req.land_value_total and not req.parcel_valuations:
            raise BadRequestException("구역 공시지가 총액이 없습니다. 지도에서 필지를 선택한 뒤 다시 계산해 주세요.")

        #종전자산 = 토지분 + 건물분
        prior_ym = _shift_ym(now_ym, prior_years)
        replacement_cost_per_m2 = cost_at(prior_ym) * PER_PYEONG_TO_PER_M2
        zone_prior = None

        if req.parcel_valuations:
            valuations = [
                ParcelValuation(
                    pnu=v.pnu,
                    land_area_m2=v.land_area_m2,
                    land_price_per_m2=v.land_price_per_m2 * prior_growth,
                    structure=v.structure,
                    building_area_m2=v.building_area_m2,
                    elapsed_years=v.elapsed_years + prior_years,
                    household_count=v.household_count,
                    has_building=v.has_building,
                    land_category=v.land_category,
                    cost_index=v.cost_index,
                    owner_type=v.owner_type,
                    housing_kind=v.housing_kind,
                    housing_price=v.housing_price,
                    housing_units=v.housing_units,
                )
                for v in req.parcel_valuations
            ]
            zone_prior = aggregate_zone(
                valuations,
                land_multiplier=appraisal_ratio,
                replacement_cost_per_m2=replacement_cost_per_m2,
                housing_rates=HOUSING_REALIZATION_RATES,
                housing_multiplier=prior_growth,
            )
            total_prior_asset = zone_prior.total
        else:
            #필지 원자료가 없으면 예전 방식 사용
            total_prior_asset = req.land_value_total * appraisal_ratio * prior_growth

        #재건축 종전자산
        apt_growth = 1.0
        recon_prior = None
        if recon and float(recon.get("official_total") or 0) > 0:
            official_ym = f"{recon.get('official_year') or now_ym[:4]}-01"
            apt_growth = escalate_sale(1.0, official_ym, prior_ym)
            recon_prior = float(recon["official_total"]) / APT_PRICE_REALIZATION_RATE * apt_growth

            #상가 등 비주거 조합원
            shop = recon.get("commercial") or {}
            shop_prior = 0.0
            if shop.get("units"):
                shop_prior = estimate(
                    land_area_m2=float(shop.get("land_share_m2") or 0),
                    land_price_per_m2=float(shop.get("land_price_per_m2") or 0) * prior_growth,
                    land_multiplier=appraisal_ratio,
                    building=BuildingSpec(
                        shop.get("structure") or "",
                        float(shop.get("floor_area_m2") or 0),
                        float(shop.get("elapsed_years") or 0) + prior_years,
                        float(shop.get("cost_index") or 1.0),
                    ),
                    replacement_cost_per_m2=replacement_cost_per_m2,
                ).total
                recon_prior += shop_prior
            total_prior_asset = recon_prior

        #의무 임대 부속토지 감정가
        if zone_prior is not None and zone_prior.dae_land_area_m2 > 0:
            rental_land_price_per_m2 = zone_prior.dae_land_total / zone_prior.dae_land_area_m2
        elif zone_prior is not None:
            rental_land_price_per_m2 = (
                zone_prior.land_total / zone_prior.land_area_m2 if zone_prior.land_area_m2 > 0 else 0.0
            )
        else:
            rental_land_price_per_m2 = total_prior_asset / req.site_area_m2 if req.site_area_m2 > 0 else 0.0

        #평형 구성
        if req.unit_mix:
            unit_mix_list = unit_mix_from_household_ratio(
                [(m.exclusive_area_m2, m.household_ratio) for m in req.unit_mix]
            )
        else:
            unit_mix_list = [UnitMix(**m) for m in UNIT_MIX]

        #임대 평형
        engine_params = dict(ENGINE_DEFAULTS_FOR_PARAMS)

        #재건축
        if project_type == ProjectType.RECONSTRUCTION:
            engine_params["base_rental_ratio"] = RECON_BASE_RENTAL_RATIO
            engine_params["uplift_rental_share"] = RECON_UPLIFT_RENTAL_SHARE
        if req.rental_exclusive_area_m2:
            engine_params["rental_exclusive_area_m2"] = req.rental_exclusive_area_m2
            engine_params["rental_supply_area_m2"] = round(
                rental_supply_from_exclusive(req.rental_exclusive_area_m2), 2
            )

        #공공기여
        if req.contribution_mix is not None:
            contribution_mix, contribution_cash_capped = normalize_mix(
                req.contribution_mix.land, req.contribution_mix.public_rental, req.contribution_mix.cash
            )
        else:
            #비율 없이 버튼 이름만 온 요청
            contribution_mix = CONTRIBUTION_PRESETS.get(req.contribution_method or "land", CONTRIBUTION_PRESETS["land"])
            contribution_cash_capped = False
        contribution_need_value = contribution_need(req.public_contribution_ratio)

        #종상향 최소 공공기여
        contribution_min_ratio = min(req.min_contribution_ratio or 0.0, req.public_contribution_ratio)
        contribution_active = contribution_need_value > 0 or contribution_min_ratio > 0

        #부지가액
        site_value_per_m2 = rental_land_price_per_m2 / appraisal_ratio * SITE_VALUE_WEIGHT if appraisal_ratio else 0.0

        #건축 대지는 공공임대 몫을 잰 뒤 정함
        net_site_area_m2 = req.site_area_m2

        #단계별 ProjectParams.
        def build_params(sale_per_m2, member_time_factor, rental_years, cost_multiplier, uplift_years=None) -> ProjectParams:
            sl = dict(sliders)
            sl["construction_cost_per_pyeong"] = round(cost_contract * cost_multiplier, 1)
            sl["general_price_per_m2"] = round(sale_per_m2, 2)

            #기타사업비
            if "other_cost_ratio" in sl:
                sl["other_cost_ratio"] = sl["other_cost_ratio"] / cost_multiplier
            ep = dict(engine_params)
            ep["member_price_time_factor"] = member_time_factor

            #임대 건물 인수가격 시점 보정
            ep["rental_cost_multiplier"] = round(rental_multiplier(_shift_ym(now_ym, rental_years)), 4)
            ep["uplift_rental_cost_multiplier"] = round(
                uplift_multiplier(_shift_ym(now_ym, rental_years if uplift_years is None else uplift_years)), 4
            )

            #의무 임대 부속토지 감정가
            ep["rental_land_price_per_m2"] = rental_land_price_per_m2
            return ProjectParams(
                name=req.name,
                project_type=project_type,
                site_area_m2=net_site_area_m2,
                member_count=req.member_count,
                unit_mix_list=unit_mix_list,
                far_base=req.far_base,
                total_prior_asset=total_prior_asset,
                **sl,
                **ep,
            )
        
        #기부면적 비율대로 기여를 채움
        land_share_per_supply = conversion_per_supply = 0.0
        rental_supply_area_m2 = 0.0
        if contribution_mix.public_rental > 0 and contribution_active:
            probe = build_params(sale_mgmt, 1.0, mgmt_years, 1.0)
            probe_areas = calc_area(probe)
            probe_costs = calc_construction_cost(probe, probe_areas, calc_allocation(probe, probe_areas))
            land_share_per_supply = (
                req.site_area_m2 * probe_areas.housing_total_m2 / probe_areas.ground_m2 / probe_areas.supply_total_m2
            )
            underground_per_supply = probe_costs.underground_m2 / probe_areas.supply_total_m2
            install_per_supply = (
                base_build_cost_per_m2(probe.rental_floor_band, probe.rental_exclusive_area_m2)
                + underground_per_supply * BASEMENT_BUILD_COST_KRW_THOUSAND / 10
            ) * rental_multiplier(prior_ym)
            conversion_per_supply = install_per_supply / site_value_per_m2 if site_value_per_m2 > 0 else 0.0
            rental_supply_area_m2 = probe.rental_supply_area_m2
        mixed = mixed_contribution(
            contribution_need_value, contribution_min_ratio, req.site_area_m2, contribution_mix,
            land_share_per_supply, conversion_per_supply,
        )

        #토지로만 내면 노드의 토지 비율 그대로
        if contribution_mix.cash <= 0 and contribution_mix.public_rental <= 0:
            contribution_land_ratio = req.public_contribution_ratio
        else:
            contribution_land_ratio = mixed.land_ratio
        contribution_cash_area_m2 = mixed.cash_area_m2

        #현금
        contribution_cash = round(contribution_cash_area_m2 * site_value_per_m2, 1)

        #공공임대 세대수
        contribution_rental_count = (
            math.ceil(mixed.rental_supply_m2 / rental_supply_area_m2) if mixed.rental_supply_m2 > 0 else 0
        )
        engine_params["contribution_cash"] = contribution_cash
        engine_params["donated_rental_count"] = contribution_rental_count
        net_site_area_m2 = req.site_area_m2 * (1 - contribution_land_ratio)

        #조합원 개인 종전자산
        owner_parcel = None
        exclusive_total = 0.0
        if req.owner.pnu and req.parcel_valuations and recon_prior is None:
            owner_parcel = next((v for v in req.parcel_valuations if v.pnu == req.owner.pnu), None)

        if recon_prior is not None:

            #재건축
            area_prices = recon.get("area_prices") or []
            if req.owner.exclusive_area_m2 and area_prices:
                _, unit_price, _ = min(area_prices, key=lambda row: abs(float(row[0]) - req.owner.exclusive_area_m2))
                owner_value = float(unit_price) / APT_PRICE_REALIZATION_RATE * apt_growth
            elif req.owner.official_price is not None:
                owner_value = req.owner.official_price / APT_PRICE_REALIZATION_RATE * apt_growth
            else:
                owner_value = total_prior_asset / max(req.member_count, 1)
            owner = OwnerInput(
                desired_unit=req.owner.desired_unit,
                appraisal_ratio=appraisal_ratio,
                appraisal_value=owner_value,
            )
        elif (
            owner_parcel is not None and owner_parcel.housing_price > 0
            and HOUSING_REALIZATION_RATES.get(owner_parcel.housing_kind)
        ):

            #내 필지가 주택이면 공시가격 ÷ 현실화율
            rate = HOUSING_REALIZATION_RATES[owner_parcel.housing_kind]
            area_prices = owner_parcel.housing_area_prices or []
            if owner_parcel.housing_kind == "공동" and req.owner.exclusive_area_m2 and area_prices:
                _, unit_price, _ = min(area_prices, key=lambda row: abs(float(row[0]) - req.owner.exclusive_area_m2))
                official = float(unit_price)
            elif owner_parcel.housing_kind == "공동":
                official = owner_parcel.housing_price / max(owner_parcel.housing_units, 1)
            else:
                official = owner_parcel.housing_price
            owner = OwnerInput(
                desired_unit=req.owner.desired_unit,
                appraisal_ratio=appraisal_ratio,
                appraisal_value=official / rate * prior_growth,
            )
        elif owner_parcel is not None:

            #집합건물이면 내 몫으로 나눔
            exclusive_total = 0.0
            if req.owner.exclusive_area_m2:
                exclusive_total = await fetch_exclusive_total_cached(req.owner.pnu)

            if req.owner.exclusive_area_m2 and exclusive_total > 0:
                share = min(req.owner.exclusive_area_m2 / exclusive_total, 1.0)
            elif owner_parcel.household_count > 1:

                #전유면적을 모르면 세대수로 균등 분할
                share = 1.0 / owner_parcel.household_count
            else:
                share = 1.0

            owner = OwnerInput(
                desired_unit=req.owner.desired_unit,
                appraisal_ratio=appraisal_ratio,
                land_area_m2=owner_parcel.land_area_m2,
                land_price_per_m2=owner_parcel.land_price_per_m2 * prior_growth,
                building_structure=owner_parcel.structure,
                building_area_m2=owner_parcel.building_area_m2 if owner_parcel.has_building else None,
                building_elapsed_years=owner_parcel.elapsed_years + prior_years,
                exclusive_share=share,
                replacement_cost_per_m2=replacement_cost_per_m2,
                building_cost_index=owner_parcel.cost_index,
            )
        elif req.owner.official_price is not None:
            owner = OwnerInput(
                desired_unit=req.owner.desired_unit,
                appraisal_ratio=appraisal_ratio,
                official_price=req.owner.official_price * prior_growth,
            )
        else:

            #1인분
            owner = OwnerInput(
                desired_unit=req.owner.desired_unit,
                appraisal_ratio=appraisal_ratio,
                appraisal_value=total_prior_asset / max(req.member_count, 1),
            )

        #관리처분 확정
        member_time_factor = sale_mgmt / sale_start if sale_start else 1.0
        stage_inputs = [
            ("관리처분 확정", sale_mgmt, 1.0, mgmt_years, 1.0, mgmt_years),
            ("분양·임대 시점 반영", sale_start, member_time_factor, start_years, 1.0, complete_years),
            ("공사비 물가변동", sale_start, member_time_factor, start_years, escalation_index, complete_years),
            ("비물가 초과 증액", sale_start, member_time_factor, start_years, escalation_total, complete_years),
        ]
        member_ratio_solved = None
        member_ratio_bound = None
        member_price_warnings = []
        if member_price_auto:
            mgmt_params = build_params(sale_mgmt, 1.0, mgmt_years, 1.0)
            mgmt_alloc = calc_allocation(mgmt_params, calc_area(mgmt_params))
            member_ratio_solved = solve_member_price_ratio(mgmt_params, mgmt_alloc, MEMBER_PRICE_TARGET_RATE)
            if member_ratio_solved is None:
                ratio = MEMBER_PRICE_RATIO_MAX
                member_price_warnings.append("조합원 분양 몫이 없어 조합원분양가 비율을 역산하지 못했습니다.")
            else:
                ratio = min(max(member_ratio_solved, MEMBER_PRICE_RATIO_MIN), MEMBER_PRICE_RATIO_MAX)
                if member_ratio_solved < MEMBER_PRICE_RATIO_MIN:
                    member_ratio_bound = "min"
                elif member_ratio_solved > MEMBER_PRICE_RATIO_MAX:
                    member_ratio_bound = "max"
            sliders["member_price_ratio"] = round(ratio, 4)

        stages = []
        for label, sale_per_m2, factor, rental_years, multiplier, uplift_years in stage_inputs:
            params = build_params(sale_per_m2, factor, rental_years, multiplier, uplift_years)
            areas = calc_area(params)
            alloc = calc_allocation(params, areas)

            #평형을 고르게 하지 않고 분양 평형 전부의 분담금을 내려줌
            units = calc_contribution_all(params, alloc, owner)

            #화면 상단 요약용 레거시 필드
            selected = next((u for u in units if u.name == owner.desired_unit), units[0])
            project = calc_project(params, alloc)
            stages.append({
                "label": label,
                "proportional_rate": round(project.proportional_rate, 2),
                "total_cost": round(project.total_cost, 1),
                "total_post_asset": round(project.total_post_asset, 1),
                "contribution": round(selected.contribution, 1),
                "unit_contributions": {u.name: round(u.contribution, 1) for u in units},
            })

        #역산 비율이 범위 끝에 걸리면 관리처분 비례율이 목표에서 벗어나면 알림
        mgmt_rate = stages[0]["proportional_rate"]
        if member_ratio_bound == "min":
            floor_text = "0원으로 낮춰도" if MEMBER_PRICE_RATIO_MIN <= 0 else f"일반분양가의 {MEMBER_PRICE_RATIO_MIN:.0%}까지 낮춰도"
            member_price_warnings.append(
                f"조합원분양가를 {floor_text} 관리처분 비례율이 "
                f"{mgmt_rate:.0f}%로 {MEMBER_PRICE_TARGET_RATE:.0f}%를 넘습니다. 사업성이 매우 높은 조건입니다."
            )
        elif member_ratio_bound == "max":
            member_price_warnings.append(
                f"조합원분양가를 일반분양가와 같게 정해도 관리처분 비례율이 {mgmt_rate:.0f}%로 "
                f"{MEMBER_PRICE_TARGET_RATE:.0f}%에 못 미칩니다. 사업성이 낮은 조건입니다."
            )

        #엔진의 통상 범위 경고는 준공 정산 비례율을 봄다
        engine_warnings = project.warnings
        final_rate = stages[-1]["proportional_rate"]
        if member_price_auto and any("통상 범위" in w for w in engine_warnings):
            engine_warnings = [w for w in engine_warnings if "통상 범위" not in w]
            change = "줄어 추가분담금으로" if final_rate < mgmt_rate else "늘어 환급으로"
            member_price_warnings.append(
                f"관리처분 비례율 {mgmt_rate:.0f}%가 준공 정산 때 {final_rate:.0f}%가 됩니다. "
                f"확정 이후 공사비 증액·분양 시점 차이로 권리가액이 종전자산의 {abs(mgmt_rate - final_rate):.0f}%만큼 {change} 돌아옵니다."
            )

        #재건축 안내
        project_warnings = []
        if project_type == ProjectType.RECONSTRUCTION:

            #재초환은 이 서비스의 계산 범위가 아님
            project_warnings.append(
                "재건축초과이익 환수 부담금은 계산하지 않았습니다. 부과 대상이면 분담금과 별도로 내야 합니다."
            )
            if recon_prior is not None:
                shop_units = int((recon.get("commercial") or {}).get("units") or 0)
                project_warnings.append(
                    f"종전자산은 아파트를 공동주택가격 ÷ 현실화율 {APT_PRICE_REALIZATION_RATE:.0%}로"
                    + (f", 상가 등 {shop_units}호를 토지 지분 + 건물 원가법으로 잡았습니다." if shop_units else " 잡았습니다.")
                )

        #공공기여 안내
        if not contribution_active:
            contribution_note = "이 용적률은 공공기여 없이 받을 수 있어 비율에 따라 결과가 달라지지 않습니다."
        else:
            parts = []
            if contribution_land_ratio > 0:
                parts.append(f"토지 {req.site_area_m2 * contribution_land_ratio:,.0f}㎡")
            if contribution_cash > 0:
                parts.append(f"현금 {contribution_cash / 10_000:,.1f}억원")
            if contribution_rental_count > 0:
                parts.append(f"공공임대 {contribution_rental_count:,}세대")
            contribution_note = (
                f"기부 내용 : {' + '.join(parts)} (기부면적 {mixed.total_m2:,.0f}㎡ 중 토지 {contribution_mix.land:.0%}"
                f" · 공공임대 {contribution_mix.public_rental:.0%} · 현금 {contribution_mix.cash:.0%})."
            )
            if contribution_cash_capped:
                contribution_note += f" 현금은 기부면적의 절반까지라 {CASH_MAX_SHARE:.0%}로 맞췄습니다."
            if contribution_cash > 0:
                contribution_note += (
                    f" 현금은 공시지가 × {SITE_VALUE_WEIGHT:g} 로 환산했고, 정비계획을 변경할 때만 "
                    "토지등소유자 과반수 동의를 받아 낼 수 있습니다 (도시정비법 시행령 제14조②)."
                )
            if contribution_rental_count > 0:
                contribution_note += (
                    " 공공임대는 인수대금이 없고, 설치비는 기본형건축비로 환산했습니다 (실제 인정 금액은 인허가 때 정해집니다)."
                )

        options = unit_options(params, alloc)
        prior_asset = estimate_prior_asset(owner)

        #임대 인수수입 분해
        revenues = calc_post_asset(params, areas, alloc)
        rental_price_announced = takeover_price_per_m2(params.rental_floor_band, params.rental_exclusive_area_m2)
        uplift_price_announced = uplift_takeover_price_per_m2(params.rental_floor_band, params.rental_exclusive_area_m2)

        member_price_ratio = sliders.get("member_price_ratio", 0.0)
        timeline = {
            "now_ym": now_ym,
            "prior_ym": prior_ym,
            "mgmt_ym": mgmt_ym,
            "start_ym": start_ym,
            "complete_ym": complete_ym,
            "prior_years": round(prior_years, 2),
            "mgmt_years": mgmt_years,
            "start_years": round(start_years, 2),
            "complete_years": complete_years,

            #공사비
            "cost_contract_per_pyeong": round(cost_contract, 1),
            "cost_final_per_pyeong": round(cost_contract * escalation_total, 1),
            "escalation_index": round(escalation_index, 4),
            "escalation_excess": round(escalation_total / escalation_index, 4),
            "escalation_total": round(escalation_total, 4),
            "excess_rate": round(excess.rate, 4),
            "excess_basis": excess.basis,
            "excess_case_count": excess.case_count,
            "excess_span": excess.span,

            #분양가
            "general_price_mgmt_per_m2": round(sale_mgmt, 2),
            "general_price_start_per_m2": round(sale_start, 2),

            #비율
            "member_price_mode": "auto" if member_price_auto else "manual",
            "member_price_target_rate": MEMBER_PRICE_TARGET_RATE if member_price_auto else None,
            "member_price_ratio_solved": round(member_ratio_solved, 4) if member_ratio_solved is not None else None,
            "member_price_ratio_bound": member_ratio_bound,
            "member_price_ratio": member_price_ratio,
            "member_price_per_m2": round(sale_mgmt * member_price_ratio, 2),
            "member_price_per_pyeong": round(sale_mgmt * member_price_ratio / PER_PYEONG_TO_PER_M2, 1),
            "public_contribution_ratio": req.public_contribution_ratio,

            #공공기여
            "contribution_mix": {
                "land": round(contribution_mix.land * 100, 1),
                "public_rental": round(contribution_mix.public_rental * 100, 1),
                "cash": round(contribution_mix.cash * 100, 1),
            },
            "contribution_total_m2": round(mixed.total_m2, 1),
            "contribution_land_ratio": round(contribution_land_ratio, 4),
            "contribution_cash": round(contribution_cash, 1),
            "contribution_cash_area_m2": round(contribution_cash_area_m2, 1),
            "contribution_rental_count": contribution_rental_count,
            "contribution_site_value_per_m2": round(site_value_per_m2, 2),
            "contribution_note": contribution_note,

            #사업 유형과 종전자산을 잡은 방법
            "project_type": project_type.value,
            "prior_basis": (
                f"공동주택가격 ÷ 현실화율 {APT_PRICE_REALIZATION_RATE:.0%}" if recon_prior is not None
                else "공시지가 × λ + 건물 원가법"
            ),
            "net_site_area_m2": round(net_site_area_m2, 1),

            #임대 인수
            "rental_takeover_basis": f"기본형건축비(지상층 + 지하층) × ({RENTAL_TAKEOVER_RATIO:.0%} + 가산비 {RENTAL_SURCHARGE_RATIO:.0%}) ({RENTAL_NOTICE}, {RENTAL_ANNOUNCED})",
            "rental_price_announced_per_m2": round(rental_price_announced, 2),
            "rental_price_mgmt_per_m2": round(rental_price_announced * round(rental_multiplier(mgmt_ym), 4), 2),
            "rental_price_final_per_m2": round(rental_price_announced * params.rental_cost_multiplier, 2),
            "rental_uplift_takeover_basis": (
                f"{UPLIFT_BASIS}" + ("" if UPLIFT_RATIO == 1 else f" × {UPLIFT_RATIO:.0%}")
                + (" (지하층 63%)" if UPLIFT_BASIS == "표준건축비" else " (지상층 + 지하층)") + f" ({UPLIFT_NOTICE})"
            ),
            "rental_uplift_price_announced_per_m2": round(uplift_price_announced, 2),
            "rental_uplift_price_final_per_m2": round(uplift_price_announced * params.uplift_rental_cost_multiplier, 2),
            "rental_base_count": alloc.base_rental_count,
            "rental_uplift_count": alloc.uplift_rental_count,
            "rental_land_price_per_m2": round(rental_land_price_per_m2, 2),
            "rental_land_area_m2": round(revenues.rental_land_area_m2, 1),
            "rental_building_revenue": round(revenues.rental_building_revenue, 1),
            "rental_base_building_revenue": round(revenues.rental_base_building_revenue, 1),
            "rental_uplift_building_revenue": round(revenues.rental_uplift_building_revenue, 1),
            "rental_underground_m2": round(revenues.rental_underground_m2, 1),
            "rental_land_revenue": round(revenues.rental_land_revenue, 1),
            "stages": stages,
        }

        #조합원 수 슬라이더 범위는 용적률에 따라 바뀜
        if req.household_count:
            member_range = member_count_range(params, alloc, req.household_count)
        else:

            #세대수 자료가 없으면 동의율 기준을 쓸 수 없음
            sale_count = sum(u.count for u in alloc.unit_types)
            low = max(1, int(sale_count * MEMBER_COUNT_UNKNOWN_MIN_RATIO))
            member_range = MemberCountRange(
                value=min(max(req.member_count, low), sale_count),
                min=low,
                max=sale_count,
                capped=True,
            )

        #종전자산 분해
        prior_asset_detail = {
            "zone_land_total": round(zone_prior.land_total, 1) if zone_prior else None,
            "zone_building_total": round(zone_prior.building_total, 1) if zone_prior else None,

            #주택 공시가격 ÷ 현실화율로 잡은 몫
            "zone_housing_total": round(zone_prior.housing_total, 1) if zone_prior else None,
            "housing_parcel_count": zone_prior.housing_parcel_count if zone_prior else None,
            "owner_basis": (
                "공시가격 ÷ 현실화율" if owner_parcel is not None and owner_parcel.housing_price > 0
                else ("토지분 + 건물분" if owner_parcel is not None else None)
            ),
            "zone_total": round(total_prior_asset, 1),
            "zone_ratio": round(zone_prior.ratio, 4) if zone_prior else None,
            "measured_member_count": zone_prior.member_count if zone_prior else None,
            "building_parcel_count": zone_prior.building_parcel_count if zone_prior else None,
            "owner_personalized": owner_parcel is not None,
            "rho": None,

            #집합건물에서 적용된 내 몫
            "owner_share": round(owner.exclusive_share, 6) if owner_parcel is not None and owner.exclusive_share else None,
            "owner_exclusive_total_m2": round(exclusive_total, 2) if owner_parcel is not None else None,
        }

        #ρ 값
        if owner_parcel is not None and zone_prior and zone_prior.ratio:
            owner_official = (
                owner_parcel.land_area_m2 * owner_parcel.land_price_per_m2 * prior_growth / 10_000
                * (owner.exclusive_share or 1.0)
            )
            if owner_official:
                prior_asset_detail["rho"] = round(
                    (prior_asset / owner_official) / zone_prior.ratio, 4
                )

    #오류 발생시 Service Unavailable Exception 발생
    except Exception as err:
        logger.warning(f"[ Log ] : Contribution API 호출에서 오류 발생 : {str(err)}")
        raise ServiceUnavailableException("Contribution API 호출에서 오류가 발생했습니다.")

    #모든 계산 결과를 검증한 뒤에만 크레딧을 차감
    result_payload = {
        "prior_asset": prior_asset,
        "proportional_rate": project.proportional_rate,
        "right_value": prior_asset * project.proportional_rate / 100,
        "member_price": selected.member_price,
        "contribution": selected.contribution,
        "project": asdict(project),
        "unit_options": [asdict(o) for o in options],
        "unit_contributions": [asdict(u) for u in units],
        "rental_exclusive_area_m2": params.rental_exclusive_area_m2,

        #종전자산 분해
        "prior_asset_detail": prior_asset_detail,
        "member_count_range": asdict(member_range),

        #준공 정산 단계별 분담금
        "timeline": timeline,
        "warnings": project_warnings + member_price_warnings + engine_warnings,
    }
    response = ContributionResponse.model_validate(
        {**result_payload, "credits_remaining": 0}
    )
    unlimited = get_active_organization(session, int(user_id)) is not None
    credits_remaining = await consume_credit_token(int(user_id), req.credit_token, unlimited=unlimited)

    #검증된 응답 모델에 잔액만 반영해 FastAPI 응답 검증 실패로 인한 오차감을 차단
    return response.model_copy(update={"credits_remaining": credits_remaining})