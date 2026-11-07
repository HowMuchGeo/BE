import asyncio
import logging
from dataclasses import asdict
from datetime import datetime
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from app.config.engine_defaults import ENGINE_DEFAULTS, HOUSING_REALIZATION_RATES
from app.database.database_connection import get_db
from app.exceptions.exceptions_handler import ServiceUnavailableException, UnauthorizedException
from app.schemas.realestate.realestate_request import ZoneRequest
from app.schemas.realestate.realestate_response import ProjectTypeResponse, ZoneResponse
from app.services.building_ledger_service import elapsed_years
from app.services.credit_service import ensure_daily_credit_available, get_daily_credits, issue_credit_token
from app.services.engine.prior_asset import ParcelValuation, aggregate_zone, building_cost_index, ordinance_member_count
from app.services.engine.project_type import RECONSTRUCTION, ParcelUse, classify_project, is_apartment
from app.services.engine.schema import PER_PYEONG_TO_PER_M2, ParcelInfo
from app.services.engine.zone import (
    FAR_TIERS, SELECTABLE_ZONING, base_zoning, build_zone_summary, business_correction_factor, far_plan,
    normalize_zoning, reconstruction_correction_factor, upzoning,
)
from app.services.land_service import (
    fetch_apartment_complex_cached, fetch_apartment_prices, fetch_building_cached, fetch_house_price,
    fetch_land_characteristics, fetch_land_possession, fetch_land_price_per_m2,
)
from app.services.organization_service import get_active_organization
from app.services.predict.construction_cost import predict_cost_per_pyeong
from app.services.predict.sale_price import predict_sale_price_per_m2
from app.services.trade_service import fetch_recent_trades
from app.utils.slider_builder import build_sliders

#백엔드 Logger
logger = logging.getLogger(__name__)

#부동산 계산식 API 라우터 설정
router = APIRouter(
    prefix="/api/v1",
    tags=["Zone Router"]
)

#주택 공시가격의 종류 : 공동주택(다세대·연립·아파트) → 공동주택가격, 단독주택(단독·다가구·다중) → 개별주택가격
def housing_kind_of(main_purpose: str) -> str:
    purpose = main_purpose or ""
    if "공동주택" in purpose:
        return "공동"
    if "단독주택" in purpose:
        return "단독"
    return ""


#사업 유형 미리 판정
@router.post(
    "/zone/type",
    response_model = ProjectTypeResponse,
    summary = "사업 유형 미리 판정 (크레딧 없음)"
)
async def get_project_type(req: ZoneRequest, request: Request):
    if request.session.get("user_id") is None:
        raise UnauthorizedException("로그인이 필요합니다.")
    hints = {h.pnu: h for h in (req.parcels or [])}
    owners = dict(zip(req.pnus, await asyncio.gather(*(fetch_land_possession(p) for p in req.pnus))))
    uses = []
    for p in req.pnus:
        land_data = await fetch_land_characteristics(p)
        hint = hints.get(p)
        bld = await fetch_building_cached(p)
        uses.append(
            ParcelUse(
                pnu=p,
                has_building=bool(bld.get("has_building")),
                main_purpose=bld.get("main_purpose") or "",
                etc_purpose=bld.get("etc_purpose") or "",
                floors=int(bld.get("floors") or 0),
                owner_type=owners.get(p, {}).get("owner_type", ""),
                land_category=land_data["land_category"],
                land_area_m2=hint.area_m2 if hint and hint.area_m2 else land_data["area_m2"],
            )
        )
    apartment_pnus = [u.pnu for u in uses if u.has_building and is_apartment(u.main_purpose, u.etc_purpose, u.floors)]
    complexes = [await fetch_apartment_complex_cached(pnu) for pnu in apartment_pnus]
    annex = {a for c in complexes for a in (c.get("annex_pnus") or [])}
    kind = classify_project(uses, annex)
    return ProjectTypeResponse(project_type=kind.project_type, project_type_reason=kind.reason)

#Pnus 정보를 바탕으로 구체적 정보를 받아오는 API
@router.post(
    "/zone",
    response_model = ZoneResponse,
    summary = "구역 선택 및 요약 집계"
)
async def get_zone(req: ZoneRequest, request: Request, session: Session = Depends(get_db)):

    #세션에서 user_id 반환
    user_id = request.session.get("user_id")

    #로그인이 안되있다면 Unauthorized Exception 발생
    if user_id is None:
        raise UnauthorizedException("로그인이 필요합니다.")

    #사용자 크레딧이 남아있는지 확인
    unlimited = get_active_organization(session, int(user_id)) is not None
    if not unlimited:
        await ensure_daily_credit_available(int(user_id))

    try:
        parcels = []

        #프론트가 지적도에서 뽑아 보낸 면적·공시지가
        hints = {h.pnu: h for h in (req.parcels or [])}

        failed_pnus = []

        #건축물대장 수집용
        valuations = []
        ledger_failed = []

        #재개발/재건축 판정용
        uses = []

        #토지 소유구분
        owners = dict(zip(req.pnus, await asyncio.gather(*(fetch_land_possession(p) for p in req.pnus))))

        #필지의 원래 용도지역과 면적
        original_zonings = []

        for p in req.pnus:
            hint = hints.get(p)
            land_data = await fetch_land_characteristics(p)
            print(land_data)

            #용도지역 조회가 실패하면 제2종일반주거로 가정
            if land_data.get("fallback"):
                failed_pnus.append(p)

            area_m2 = hint.area_m2 if hint and hint.area_m2 else land_data["area_m2"]

            #공시지가
            if hint and hint.land_price_per_m2:
                price = hint.land_price_per_m2
            else:
                price = land_data["land_price_per_m2"] or await fetch_land_price_per_m2(p)

            #용도지역
            zoning = req.zoning or land_data["zoning"]
            if not land_data.get("fallback"):
                original_zonings.append((land_data["zoning"], area_m2))

            parcels.append(
                ParcelInfo(
                    pnu=p,
                    area_m2=area_m2,
                    land_price_per_m2=price,
                    zoning=zoning,
                    land_category=land_data["land_category"]
                )
            )

            #건축물대장
            bld = await fetch_building_cached(p)
            if bld.get("fallback"):
                ledger_failed.append(p)

            owner_type = owners.get(p, {}).get("owner_type", "")

            uses.append(
                ParcelUse(
                    pnu=p,
                    has_building=bool(bld.get("has_building")),
                    main_purpose=bld.get("main_purpose") or "",
                    etc_purpose=bld.get("etc_purpose") or "",
                    floors=int(bld.get("floors") or 0),
                    owner_type=owner_type,
                    land_category=land_data["land_category"],
                    land_area_m2=area_m2,
                )
            )

            valuations.append(
                ParcelValuation(
                    pnu=p,
                    land_area_m2=area_m2,
                    land_price_per_m2=price,
                    structure=bld.get("structure") or "",
                    building_area_m2=float(bld.get("floor_area_m2") or 0.0),
                    elapsed_years=float(bld.get("elapsed_years") or 0.0),

                    #조합원 수
                    household_count=ordinance_member_count(
                        bld.get("main_purpose") or "",
                        bld.get("approval_ymd") or "",
                        int(bld.get("household_count") or 1),
                        int(owners.get(p, {}).get("owner_count") or 0),
                        owners.get(p, {}).get("owners_before_1997"),
                    ),
                    has_building=bool(bld.get("has_building")),
                    land_category=land_data["land_category"],

                    #재조달원가 상대지수
                    cost_index=building_cost_index(
                        bld.get("structure") or "",
                        bld.get("main_purpose") or "",
                        int(bld.get("floors") or 0),
                        bld.get("etc_purpose") or "",
                    ) if bld.get("has_building") else 1.0,
                    owner_type=owner_type,
                )
            )

        #주택 공시가격
        kinds = {u.pnu: housing_kind_of(u.main_purpose) if u.has_building else "" for u in uses}
        housing_pnus = [p for p, k in kinds.items() if k]
        housing_prices = dict(zip(housing_pnus, await asyncio.gather(*(
            fetch_apartment_prices(p) if kinds[p] == "공동" else fetch_house_price(p) for p in housing_pnus
        ))))
        for v in valuations:
            info = housing_prices.get(v.pnu)
            if not info or info.get("fallback"):
                continue
            v.housing_kind = kinds[v.pnu]
            if v.housing_kind == "공동":
                v.housing_price = float(info.get("official_total") or 0.0)
                v.housing_units = int(info.get("unit_count") or 0)
            else:
                v.housing_price = float(info.get("house_price") or 0.0)

        target_ym = req.target_ym or datetime.now().strftime("%Y-%m")
        zone = build_zone_summary(parcels)

        #사용자가 용도지역을 직접 골랐으면 조회 실패는 용적률에 영향을 주지 않음
        if failed_pnus and not req.zoning:
            zone.warnings.append(
                f"{len(failed_pnus)}개 필지의 용도지역을 불러오지 못해 제2종일반주거지역으로 가정했습니다. "
                "용적률이 실제와 다를 수 있습니다. 용도지역을 직접 선택하면 이 가정을 덮어씁니다."
            )

        #종상향
        base = base_zoning(original_zonings) if req.zoning else None
        upzone = upzoning(base, req.zoning)

        #사업성 보정계수
        now = datetime.now()
        parcel_year = now.year if now.month >= 6 else now.year - 1

        #사업 유형
        apartment_pnus = [u.pnu for u in uses if u.has_building and is_apartment(u.main_purpose, u.etc_purpose, u.floors)]
        complexes = {pnu: await fetch_apartment_complex_cached(pnu) for pnu in apartment_pnus}
        annex = {a for c in complexes.values() for a in (c.get("annex_pnus") or [])}
        kind = classify_project(uses, annex)

        #건물이 하나도 없는 선택
        if not any(u.has_building for u in uses) and any(u.land_category in ("대", "임야") for u in uses):
            zone.warnings.append(
                "고른 필지에 건축물대장이 없습니다. 아파트 단지라면 대장이 단지의 대표지번 한 곳에 등록돼 있을 수 있어, "
                "그 필지도 함께 골라야 단지로 인식됩니다."
            )

        reconstruction = None
        current_far = None
        if kind.project_type == RECONSTRUCTION:

            #재건축 단지 = 아파트 필지 + 부속지번
            complex_pnus = set(kind.apartment_pnus) | set(kind.annex_pnus)
            complex_parcels = [p for p in parcels if p.pnu in complex_pnus]
            complex_land = sum(p.area_m2 for p in complex_parcels)

            #조합원 수 = 총괄표제부 세대수
            households = sum(int(c.get("households") or 0) for c in complexes.values()) or sum(
                v.household_count for v in valuations if v.pnu in kind.apartment_pnus
            )
            #현황용적률 = 용적률 산정 연면적 ÷ 단지 대지면적
            far_area = sum(float(c.get("far_area_m2") or 0) for c in complexes.values())
            current_far = far_area / complex_land * 100 if complex_land > 0 and far_area > 0 else None

            prices = [await fetch_apartment_prices(pnu) for pnu in kind.apartment_pnus]
            merged: dict[float, list] = {}
            for price in prices:
                for area, median_price, count in price.get("area_prices") or []:
                    slot = merged.setdefault(float(area), [0.0, 0])
                    slot[0] += median_price * count
                    slot[1] += count

            #상가 등 비주거 조합원
            shop_units = sum(int(c.get("commercial_units") or 0) for c in complexes.values())
            commercial = None
            if shop_units:
                shop_area = sum(float(c.get("commercial_floor_area_m2") or 0) for c in complexes.values())
                total_area = sum(float(c.get("total_floor_area_m2") or 0) for c in complexes.values())
                main_shop = max(complexes.values(), key=lambda c: float(c.get("commercial_floor_area_m2") or 0))
                structure = main_shop.get("commercial_structure") or ""
                commercial = {
                    "units": shop_units,
                    "floor_area_m2": round(shop_area, 1),
                    "land_share_m2": round(complex_land * shop_area / total_area, 1) if total_area > 0 else 0.0,
                    "land_price_per_m2": round(
                        sum(p.area_m2 * p.land_price_per_m2 for p in complex_parcels) / complex_land
                    ) if complex_land > 0 else 0,
                    "structure": structure,
                    "elapsed_years": round(elapsed_years(main_shop.get("commercial_approval_ymd") or ""), 2),
                    "cost_index": building_cost_index(structure, "근린생활시설"),
                }

            reconstruction = {
                #조합원 = 아파트 세대 + 상가 등 비주거 호수
                "members": households + shop_units,
                "commercial": commercial,
                "households": households,
                "site_area_m2": round(complex_land, 1),
                "current_far": round(current_far, 1) if current_far else None,
                "official_year": max((p.get("year") or 0) for p in prices) or None,
                "unit_count": sum(int(p.get("unit_count") or 0) for p in prices),
                "official_total": round(sum(float(p.get("official_total") or 0) for p in prices), 1),

                #전용면적별 호당 공시가격
                "area_prices": [[a, round(v[0] / v[1], 1), v[1]] for a, v in sorted(merged.items()) if v[1]],
                "annex_pnus": kind.annex_pnus,
            }
            if any(p.get("fallback") for p in prices):
                zone.warnings.append(
                    "공동주택가격을 불러오지 못한 단지가 있어 재건축 종전자산이 과소할 수 있습니다."
                )

            #재건축 보정계수 = 서울시 공동주택 평균 공시지가 ÷ 단지 평균 + α(대지면적) + β(세대밀도)
            tiers = FAR_TIERS.get(normalize_zoning(zone.zoning or ""))
            correction = reconstruction_correction_factor(
                [(p.land_category, p.area_m2, p.land_price_per_m2) for p in complex_parcels],
                parcel_year=parcel_year,
                site_area_m2=complex_land,
                households=households,
                available_far=(float(tiers[1]) + float(tiers[3])) / 2 if tiers else float(zone.far_max),
            )
        else:
            correction = business_correction_factor(
                [(p.land_category, p.area_m2, p.land_price_per_m2) for p in parcels],
                parcel_year=parcel_year,
            )

        #용적률 계획 : 4단 + 보정계수 + 종상향 + 노드별 상한용적률 공공기여
        plan = far_plan(zone.zoning, zone.far_min, zone.far_max, correction.factor, base_zoning=base,
                        current_far=current_far)
        if plan.note:
            zone.warnings.append(plan.note)

        #공사비와 분양가는 같은 시점으로 예측
        cost = predict_cost_per_pyeong(target_ym, region=zone.region)

        #분양가 : 인근 분양 사례 + 실거래 추세 보정
        lawd_cd = zone.pnus[0][:5] if zone.pnus else ""
        trades = await fetch_recent_trades(lawd_cd, target_ym) if lawd_cd else []
        sale = predict_sale_price_per_m2(target_ym, trades=trades, region=zone.region)

        #종전자산 = 토지분 + 건물분
        replacement_cost_per_m2 = cost.cost_per_pyeong * PER_PYEONG_TO_PER_M2
        prior = aggregate_zone(
            valuations,
            land_multiplier=ENGINE_DEFAULTS["appraisal_ratio"],
            replacement_cost_per_m2=replacement_cost_per_m2,
            housing_rates=HOUSING_REALIZATION_RATES,
        )

        #건축물대장을 못 받은 필지는 나대지로 떨어짐
        if ledger_failed:
            zone.warnings.append(
                f"{len(ledger_failed)}개 필지의 건축물대장을 불러오지 못해 건물분을 0 으로 두었습니다. "
                "그 필지는 나대지로 계산됩니다."
            )
        zone.warnings.extend(prior.warnings)

    #오류 발생시 Service Unavailable Exception 발생
    except Exception as e:
        raise ServiceUnavailableException(f"Zone API 호출에서 오류 발생.")

    result = {
        "zone": asdict(zone),

        #제54조 임대 의무의 기준점 = 정비계획 상한용적률 (보정계수 반영)

        "far_base": plan.ceiling,
        "target_ym": target_ym,

        #프론트 용도지역 버튼 목록
        "zoning_options": SELECTABLE_ZONING,
        "selected_zoning": req.zoning,
        "sliders": build_sliders(
            zone, cost, req.household_count, project_type=kind.project_type, sale=sale,

            #재건축 조합원 수 = 총괄표제부 세대수 + 상가 호수
            measured_member_count=(
                reconstruction["members"] if reconstruction and reconstruction["members"]

                #건축물대장으로 센 실측값을 사용
                else (prior.member_count if (prior.building_parcel_count or prior.housing_parcel_count) else None)
            ),
            far=plan,
        ),

        #사업 유형 판정
        "project_type": kind.project_type,
        "project_type_reason": kind.reason,
        "reconstruction": reconstruction,

        #사업성 보정계수와 종상향 판정
        "business_correction": asdict(correction),
        "upzoning": asdict(upzone),
        "cost_prediction": asdict(cost),
        "sale_prediction": asdict(sale),

        #종전자산(토지분+건물분)과 실측 조합원 수
        "prior_asset": {
            "land_total": round(prior.land_total, 1),
            "building_total": round(prior.building_total, 1),
            "total": round(prior.total, 1),
            "official_total": round(prior.official_total, 1),
            "housing_total": round(prior.housing_total, 1),
            "housing_parcel_count": prior.housing_parcel_count,
            "ratio": round(prior.ratio, 4),
            "member_count": prior.member_count,
            "parcel_count": prior.parcel_count,
            "building_parcel_count": prior.building_parcel_count,
            "replacement_cost_per_m2": round(replacement_cost_per_m2, 2),
            "parcels": [
                {
                    "pnu": v.pnu,
                    "land_area_m2": round(v.land_area_m2, 2),
                    "land_price_per_m2": v.land_price_per_m2,
                    "structure": v.structure,
                    "building_area_m2": round(v.building_area_m2, 2),
                    "elapsed_years": round(v.elapsed_years, 2),
                    "household_count": v.household_count,
                    "has_building": v.has_building,
                    "land_category": v.land_category,
                    "cost_index": round(v.cost_index, 4),
                    "owner_type": v.owner_type,

                    #주택 공시가격
                    "housing_kind": v.housing_kind,
                    "housing_price": v.housing_price,
                    "housing_units": v.housing_units,
                    "housing_area_prices": (housing_prices.get(v.pnu) or {}).get("area_prices") if v.housing_kind == "공동" else None,
                }
                for v in valuations
            ],
        },
    }

    #실제 차감은 /contribution 계산과 응답 검증이 성공한 후에 진행
    if unlimited:
        result["credits_remaining"] = -1
    else:
        credits = await get_daily_credits(int(user_id))
        result["credits_remaining"] = credits["credits_remaining"]
    result["credit_token"] = "pending"
    response = ZoneResponse.model_validate(result)
    response = response.model_copy(
        update={"credit_token": await issue_credit_token(int(user_id))}
    )

    #결과 반환
    return response