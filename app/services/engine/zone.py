import math
from dataclasses import dataclass
from app.services.engine import policy
from app.services.engine.schema import ParcelInfo, ZoneSummary

#용도지역 -> (조례 용적률, 법적상한용적률) %
FAR_TABLE = {
    #주거지역
    "제1종전용주거지역": (100, 100),
    "제2종전용주거지역": (120, 150),
    "제1종일반주거지역": (150, 200),
    "제2종일반주거지역": (200, 250),
    "제3종일반주거지역": (250, 300),
    "준주거지역":       (400, 500),

    #상업지역
    "중심상업지역":     (1000, 1500),
    "일반상업지역":     (800, 1300),
    "근린상업지역":     (600, 900),
    "유통상업지역":     (600, 1100),

    #공업지역
    "전용공업지역":     (200, 300),
    "일반공업지역":     (200, 350),
    "준공업지역":       (400, 400),

    #녹지지역
    "보전녹지지역":     (50, 80),
    "생산녹지지역":     (50, 100),
    "자연녹지지역":     (50, 100),
}

#용도지역 -> (기준, 허용, 상한, 법적상한) % — 2030 기본계획 4단 체계
FAR_TIERS = {z: tuple(v) for z, v in policy.RULES["far_tiers"].items()}

#종상향 (원래 -> 변경) 4단
FAR_TIERS_UPZONED = {
    (base, sel): tuple(v)
    for base, targets in policy.RULES["far_tiers_upzoned"].items()
    for sel, v in targets.items()
}

#표에 없는 종상향 조합의
UPZONED_BASE = {z: tuple(v) for z, v in policy.RULES["upzoned_base"].items()}

#기준 -> 허용 인센티브량
def far_incentive(zoning: str) -> float:
    tiers = FAR_TIERS.get(normalize_zoning(zoning))
    return (tiers[1] - tiers[0]) if tiers else 0.0

#서울시 평균 공시지가
SEOUL_AVG_LAND_PRICE_REDEV = {int(y): int(v) for y, v in policy.RULES["seoul_avg_land_price_redev"].items()}

#서울시 공동주택 평균 공시지가
SEOUL_AVG_LAND_PRICE_RECON = {int(y): int(v) for y, v in policy.RULES["seoul_avg_land_price_recon"].items()}

#재건축 α, β, 과밀단지 기준
RECON_SITE_FACTOR = policy.RULES["recon_site_factor"]
RECON_DENSITY_FACTOR = policy.RULES["recon_density_factor"]
RECON_OVERDENSE_INCENTIVE = float(policy.RULES["recon_overdense_incentive"])

#과밀단지 준주거 종상향 체계
RECON_OVERDENSE_UPZONE = policy.RULES["recon_overdense_upzone"]

#보정계수 범위
CORRECTION_MIN = float(policy.RULES["correction_min"])
CORRECTION_MAX = float(policy.RULES["correction_max"])

@dataclass
class BusinessCorrection:
    factor: float
    raw: float | None
    zone_avg_price: float | None
    seoul_avg_price: float
    basis_year: int
    parcel_count: int
    note: str = ""
    project_type: str = "재개발"
    land_factor: float | None = None
    site_factor: float = 0.0
    density_factor: float = 0.0

#재개발 사업성 보정계수
def business_correction_factor(
    parcels: list[tuple[str, float, float]], parcel_year: int
) -> BusinessCorrection:
    basis_year, seoul, zone_avg_basis, lot_count = _land_price_basis(parcels, parcel_year, SEOUL_AVG_LAND_PRICE_REDEV)
    if zone_avg_basis is None:
        return BusinessCorrection(1.0, None, None, seoul, basis_year, 0,
                                  "구역에 지목 '대' 필지가 없어 보정계수를 1.0 으로 두었습니다.")

    raw = seoul / zone_avg_basis
    clamped = min(max(raw, CORRECTION_MIN), CORRECTION_MAX)
    factor = _ceil2(clamped)
    factor = min(factor, CORRECTION_MAX)
    return BusinessCorrection(factor, round(raw, 4), round(zone_avg_basis), seoul, basis_year, lot_count,
                              land_factor=_ceil2(raw))

#소수 셋째자리 올림
def _ceil2(value: float) -> float:
    return math.ceil(round(value * 100, 6)) / 100

#서울시 평균과 구역 평균을 같은 해로 맞춤
def _land_price_basis(parcels, parcel_year: int, series: dict[int, int]):
    basis_year = max(y for y in series if y < parcel_year) if any(y < parcel_year for y in series) else max(series)
    seoul = float(series[basis_year])
    lots = [(area, price) for category, area, price in parcels if category == "대" and area > 0 and price > 0]
    if not lots:
        return basis_year, seoul, None, 0
    zone_avg = sum(a * p for a, p in lots) / sum(a for a, _ in lots)
    trend = SEOUL_AVG_LAND_PRICE_REDEV
    years = sorted(trend)
    growth = (
        (trend[years[-1]] / trend[years[-2]]) ** (1 / (years[-1] - years[-2]))
        if len(years) >= 2 else 1.0
    )
    gap = max(parcel_year - basis_year, 0)
    return basis_year, seoul, zone_avg / (growth ** gap), len(lots)

#재건축 사업성 보정계수 = 공시지가 보정계수 + α + β
def reconstruction_correction_factor(
    parcels: list[tuple[str, float, float]],
    parcel_year: int,
    site_area_m2: float,
    households: int,
    available_far: float | None,
) -> BusinessCorrection:
    basis_year, seoul, zone_avg_basis, lot_count = _land_price_basis(parcels, parcel_year, SEOUL_AVG_LAND_PRICE_RECON)
    if zone_avg_basis is None:
        return BusinessCorrection(1.0, None, None, seoul, basis_year, 0,
                                  "단지에 지목 '대' 필지가 없어 보정계수를 1.0 으로 두었습니다.", project_type="재건축")

    raw_land = seoul / zone_avg_basis
    land = _ceil2(raw_land)

    a = RECON_SITE_FACTOR
    if site_area_m2 <= 0 or site_area_m2 >= a["min_at_m2"]:
        alpha = 0.0
    elif site_area_m2 <= a["full_at_or_below_m2"]:
        alpha = a["max"]
    else:
        t = (site_area_m2 - a["full_at_or_below_m2"]) / (a["min_at_m2"] - a["full_at_or_below_m2"])
        alpha = a["max"] + (a["min"] - a["max"]) * t

    b = RECON_DENSITY_FACTOR
    beta = 0.0
    if households > 0 and available_far and site_area_m2 > 0:
        supply_per_household = available_far / 100 * site_area_m2 / households
        if supply_per_household < b["full_below_supply_m2"]:
            beta = b["max"]
        elif supply_per_household < b["min_at_supply_m2"]:
            t = (supply_per_household - b["full_below_supply_m2"]) / (b["min_at_supply_m2"] - b["full_below_supply_m2"])
            beta = b["max"] + (b["min"] - b["max"]) * t

    alpha, beta = _ceil2(alpha) if alpha else 0.0, _ceil2(beta) if beta else 0.0
    raw = land + alpha + beta
    factor = min(max(raw, CORRECTION_MIN), CORRECTION_MAX)
    return BusinessCorrection(factor, round(raw, 4), round(zone_avg_basis), seoul, basis_year, lot_count,
                              project_type="재건축", land_factor=land, site_factor=alpha, density_factor=beta)

#주거지역 용도지역 단계 (아래 -> 위)
ZONING_LADDER = [
    "제1종전용주거지역",
    "제2종전용주거지역",
    "제1종일반주거지역",
    "제2종일반주거지역",
    "제3종일반주거지역",
    "준주거지역",
]

#종상향 최소 공공기여율
UPZONING_CONTRIBUTION = {
    (base, sel): float(v)
    for base, targets in policy.RULES["upzoning_contribution"].items()
    for sel, v in targets.items()
}

#전용주거 종상향 하한 기준
UPZONING_FALLBACK_BASE = policy.RULES["upzoning_fallback_base"]

#필지 원래 용도지역의 면적가중 평균 단계
def base_zoning(zoning_areas: list[tuple[str, float]]) -> str | None:
    ranked = [
        (ZONING_LADDER.index(normalize_zoning(z)), a)
        for z, a in zoning_areas
        if normalize_zoning(z) in ZONING_LADDER and a and a > 0
    ]
    if not ranked:
        return None
    mean = sum(r * a for r, a in ranked) / sum(a for _, a in ranked)
    return ZONING_LADDER[min(int(math.floor(mean + 0.5)), len(ZONING_LADDER) - 1)]

@dataclass
class Upzoning:
    base_zoning: str | None
    selected_zoning: str | None 
    steps: int
    contribution_ratio: float
    note: str = ""

def upzoning(base: str | None, selected: str | None) -> Upzoning:
    sel = normalize_zoning(selected or "")
    if not base or sel not in ZONING_LADDER:
        return Upzoning(base, selected, 0, 0.0)

    steps = ZONING_LADDER.index(sel) - ZONING_LADDER.index(base)
    if steps <= 0:
        return Upzoning(base, sel, steps, 0.0)

    ratio = UPZONING_CONTRIBUTION.get((base, sel))
    fallback = ratio is None
    if fallback:
        ratio = UPZONING_CONTRIBUTION.get((UPZONING_FALLBACK_BASE, sel), 0.0)

    note = f"{base} → {sel} 종상향 : 공공기여 최소 {ratio:.0%} (서울시 2030 기본계획)."
    if fallback:
        note += f" {base} 에서 올리는 비율은 서울시 표에 없어 1종일반 기준을 하한으로 썼습니다."
    return Upzoning(base, sel, steps, ratio, note)

#=== 용적률 계획 (4단 + 보정계수 + 종상향 + 공공기여) ===#
LAND_CONTRIBUTION_COEF = float(policy.RULES["land_contribution_coef"])
EXCESS_CONTRIBUTION_TO_LEGAL = bool(policy.RULES["excess_contribution_to_legal"])

#far 까지 필요한 토지 기부 비율
def land_contribution_for(far: float, allowed: float, ceiling: float, allowed_base: float | None = None) -> float:
    base = allowed_base or allowed
    target = min(far, ceiling)
    if allowed <= 0 or base <= 0 or target <= allowed:
        return 0.0
    alpha = (target - allowed) / (base * LAND_CONTRIBUTION_COEF)
    return alpha / (1 + alpha)

#토지 기부 비율 -> 용적률
def far_for_contribution(contribution: float, allowed: float, ceiling: float, allowed_base: float | None = None) -> float:
    base = allowed_base or allowed
    if contribution <= 0:
        return allowed
    alpha = contribution / (1 - contribution)
    return min(allowed + base * LAND_CONTRIBUTION_COEF * alpha, ceiling)

@dataclass
class FarNode:
    value: float
    label: str
    contribution: float

@dataclass
class FarPlan:
    nodes: list[FarNode]
    allowed: float
    ceiling: float
    legal: float
    min_contribution: float
    note: str = ""
    allowed_base: float = 0.0
    overdense: bool = False

#용적률 노드 생성
def far_plan(
    zoning: str | None,
    far_min: float,
    far_max: float,
    correction: float = 1.0,
    base_zoning: str | None = None,
    current_far: float | None = None,
) -> FarPlan:
    sel = normalize_zoning(zoning or "")
    up = upzoning(base_zoning, sel) if base_zoning else Upzoning(None, sel, 0, 0.0)
    min_c = up.contribution_ratio if up.steps > 0 else 0.0
    note = ""

    tiers = None
    if up.steps > 0:
        base = base_zoning if base_zoning in UPZONED_BASE else UPZONING_FALLBACK_BASE
        tiers = FAR_TIERS_UPZONED.get((base, sel))
        if tiers is None and sel in FAR_TIERS:
            tiers = UPZONED_BASE[base] + FAR_TIERS[sel][2:]
            note = (
                f"{base} → {sel} 용적률은 서울시 표에 없는 조합이라 표의 원칙"
                "(기준·허용은 원래 용도지역, 상한·법적상한은 변경 용도지역)으로 계산했습니다."
            )
        if tiers is not None and base != base_zoning:
            note = (note + " " if note else "") + f"{base_zoning} 에서 올리는 용적률은 서울시 표에 없어 1종일반 행으로 계산했습니다."
    if tiers is None:
        tiers = FAR_TIERS.get(sel)

    if tiers is None:
        lo, hi = float(far_min), float(far_max)
        nodes = [FarNode(lo, "상한·법적상한" if lo == hi else "상한", min_c)]
        if hi > lo:
            nodes.append(FarNode(hi, "법적상한", min_c))
        return FarPlan(nodes, lo, lo, hi, min_c, note, allowed_base=lo)

    base_far, allowed, ceiling, legal = (float(v) for v in tiers)
    k = min(max(float(correction), CORRECTION_MIN), CORRECTION_MAX)
    lift = (allowed - base_far) * (k - 1.0)
    allowed_k = min(allowed + lift, legal)

    #과밀단지 준주거 종상향
    ou = RECON_OVERDENSE_UPZONE
    base_legal = float(FAR_TIERS[ou["base_zoning"]][3]) if ou["base_zoning"] in FAR_TIERS else None
    if (
        current_far is not None and base_legal is not None and up.steps > 0
        and base_zoning == ou["base_zoning"] and sel == ou["upzoned"]
        and current_far + RECON_OVERDENSE_INCENTIVE * (k - 1.0) >= base_legal
    ):
        upzone_legal = float(ou["legal_max"])
        allowed_k = min(current_far + RECON_OVERDENSE_INCENTIVE * (k - 1.0), float(ou["allowed_max"]))
        legal_k = min(allowed_k * float(ou["legal_ratio"]), upzone_legal)
        ceiling_k = min(float(ou["ceiling_max"]), legal_k)
        standard_ratio = UPZONING_CONTRIBUTION.get((ou["base_zoning"], ou["upzoned"]), min_c)
        min_c = standard_ratio * max(legal_k - base_legal, 0.0) / (upzone_legal - base_legal)
        note = (note + " " if note else "") + (
            f"현황용적률 {current_far:.0f}% 과밀단지를 준주거로 종상향해 현황을 허용용적률로 인정하고, "
            f"법적상한을 허용의 125%({legal_k:.0f}%)로 제한했습니다. 종상향 공공기여 {min_c:.1%} (서울시 세부기준)."
        )
        contribution_cap = legal_k if EXCESS_CONTRIBUTION_TO_LEGAL else ceiling_k
        floor_far = round(far_for_contribution(min_c, allowed_k, contribution_cap, allowed), 1)
        raw = [("종상향 최소", floor_far)] + [(n, v) for n, v in (("상한", ceiling_k), ("법적상한", legal_k)) if v > floor_far]
        merged: dict[float, list[str]] = {}
        for name, value in raw:
            merged.setdefault(round(value, 1), []).append(name)
        nodes = [
            FarNode(value, "·".join(names), max(land_contribution_for(value, allowed_k, contribution_cap, allowed), min_c))
            for value, names in sorted(merged.items())
        ]
        return FarPlan(nodes, allowed_k, contribution_cap, legal_k, min_c, note, allowed_base=allowed, overdense=True)

    #과밀단지는 현황용적률을 허용으로 인정
    overdense = current_far is not None and current_far > allowed
    if overdense:
        allowed_k = min(max(allowed_k, current_far + RECON_OVERDENSE_INCENTIVE * (k - 1.0)), legal)
        note = (note + " " if note else "") + (
            f"현황용적률 {current_far:.0f}% 가 허용용적률 {allowed:.0f}% 보다 높은 과밀단지라 "
            f"현황용적률을 허용용적률로 인정했습니다 (허용 {allowed_k:.0f}%)."
        )
        if sel == ou["base_zoning"] and current_far + RECON_OVERDENSE_INCENTIVE * (k - 1.0) >= legal:
            note += f" 준주거로 종상향하면 허용을 현황까지, 법적상한을 그 125%까지 받을 수 있습니다 (서울시 세부기준)."
    ceiling_k = min(max(ceiling + lift, allowed_k), legal)

    #공공기여 상한
    contribution_cap = legal if EXCESS_CONTRIBUTION_TO_LEGAL else ceiling_k

    raw = [("기준", base_far), ("허용", allowed_k), ("상한", ceiling_k), ("법적상한", legal)]
    if min_c > 0:
        floor_far = round(far_for_contribution(min_c, allowed_k, contribution_cap, allowed), 1)
        raw = [("종상향 최소", floor_far)] + [(n, v) for n, v in raw if n in ("상한", "법적상한") and v > floor_far]

    merged: dict[float, list[str]] = {}
    for name, value in raw:
        merged.setdefault(round(value, 1), []).append(name)
    nodes = [
        FarNode(value, "·".join(names), max(land_contribution_for(value, allowed_k, contribution_cap, allowed), min_c))
        for value, names in sorted(merged.items())
    ]
    return FarPlan(nodes, allowed_k, contribution_cap, legal, min_c, note, allowed_base=allowed, overdense=overdense)

#저밀도 용도지역
LOW_DENSITY_ZONING = ("보전녹지지역", "생산녹지지역", "자연녹지지역")

#용도지역별 상가 비율 상한
COMMERCIAL_RATIO_MAX = {
    "제1종전용주거지역": 0.03,
    "제2종전용주거지역": 0.03,
    "제1종일반주거지역": 0.05,
    "제2종일반주거지역": 0.10,
    "제3종일반주거지역": 0.10,
    "준주거지역":       0.30,
}

#용도지역 미상 상가 비율 상한
DEFAULT_COMMERCIAL_RATIO_MAX = 0.10


#선택 가능한 용도지역
SELECTABLE_ZONING = [
    "제1종전용주거지역",
    "제2종전용주거지역",
    "제1종일반주거지역",
    "제2종일반주거지역",
    "제3종일반주거지역",
    "준주거지역",
]

#종전자산 제외 지목
EXCLUDED_LAND_CATEGORY = {"도로", "구거", "하천", "공원", "제방"}

#시군구 코드 -> 이름
SIGUNGU_NAME = {
    "11350": "노원구",
    "11305": "강북구",
    "11320": "도봉구",
    "11230": "동대문구",
    "11260": "중랑구",
    "11215": "광진구",
    "11110": "종로구",
    "11140": "중구",
    "11380": "은평구",
    "11410": "서대문구",
    "11440": "마포구",
    "11170": "용산구",
    "11200": "성동구",
    "11290": "성북구",
}

#용도지역 문자열 정리
def normalize_zoning(zoning: str) -> str:
    return "".join(zoning.split()) if zoning else ""

#PNU -> 시군구 코드
def sigungu_code(pnu: str) -> str:
    return pnu[:5] if pnu and len(pnu) >= 5 else ""

#구역 대표 시군구
def district(parcels: list[ParcelInfo]) -> str | None:
    area_by_code: dict[str, float] = {}
    for parcel in parcels:
        code = sigungu_code(parcel.pnu)
        if not code:
            continue
        area = parcel.area_m2 if parcel.area_m2 and parcel.area_m2 > 0 else 0.0
        area_by_code[code] = area_by_code.get(code, 0.0) + area

    if not area_by_code:
        return None

    #면적이 가장 큰 시군구
    top_code = max(area_by_code, key=lambda c: area_by_code[c])
    return SIGUNGU_NAME.get(top_code)

#필지 목록 -> 구역 집계
def build_zone_summary(parcels: list[ParcelInfo]) -> ZoneSummary:
    if not parcels:
        raise ValueError("선택된 필지가 없습니다.")

    warnings = []

    site_area_m2 = 0.0
    land_value_total = 0.0
    far_min_weighted = 0.0
    far_max_weighted = 0.0
    far_area_m2 = 0.0
    area_by_zoning: dict[str, float] = {}
    low_density_m2 = 0.0
    low_density_count = 0

    for parcel in parcels:

        #면적 없는 필지 제외
        if parcel.area_m2 is None or parcel.area_m2 <= 0:
            warnings.append(f"면적이 없는 필지를 제외했습니다: {parcel.pnu}")
            continue

        site_area_m2 += parcel.area_m2

        #제외 지목
        if parcel.land_category in EXCLUDED_LAND_CATEGORY:
            pass
        else:
            #원 -> 만원
            land_value_total += parcel.area_m2 * parcel.land_price_per_m2 / 10_000

        #용도지역별 용적률 면적 가중평균
        zoning = normalize_zoning(parcel.zoning)
        area_by_zoning[zoning] = area_by_zoning.get(zoning, 0.0) + parcel.area_m2
        far_range = FAR_TABLE.get(zoning)
        if far_range is None:
            warnings.append(f"용적률 표에 없는 용도지역입니다: {parcel.zoning} ({parcel.pnu})")
            continue

        if zoning in LOW_DENSITY_ZONING:
            low_density_m2 += parcel.area_m2
            low_density_count += 1

        far_min_weighted += parcel.area_m2 * far_range[0]
        far_max_weighted += parcel.area_m2 * far_range[1]
        far_area_m2 += parcel.area_m2

    if site_area_m2 <= 0:
        raise ValueError("면적이 있는 필지가 하나도 없습니다.")

    #용도지역 미확인
    if far_area_m2 <= 0:
        raise ValueError("용도지역을 확인할 수 있는 필지가 없습니다.")

    #반올림
    far_min = round(far_min_weighted / far_area_m2, 2)
    far_max = round(far_max_weighted / far_area_m2, 2)

    #용도지역 혼재 안내
    if len(area_by_zoning) > 1:
        parts = []
        for zoning, area in sorted(area_by_zoning.items(), key=lambda kv: -kv[1]):
            share = area / site_area_m2 * 100

            #1% 미만 표기
            parts.append(f"{zoning or '미확인'} {share:.0f}%" if share >= 1 else f"{zoning or '미확인'} 1%미만")
        warnings.append(
            f"용도지역 구성: {', '.join(parts)} — 면적 가중평균으로 용적률을 계산했습니다."
        )

    #저밀도 용도지역 경고
    if low_density_count:
        warnings.append(
            f"이 중 녹지지역 {low_density_count}개 필지는 용적률이 50%라 평균을 끌어내립니다. "
            f"정비사업 대상이 아니라면 선택에서 빼세요."
        )

    #대표 시군구
    region = district(parcels)
    codes = {sigungu_code(p.pnu) for p in parcels if sigungu_code(p.pnu)}
    if len(codes) > 1:
        warnings.append(f"여러 시군구에 걸친 구역입니다. 면적이 가장 큰 {region or '지역'} 기준으로 처리했습니다.")
    if region is None:
        warnings.append("PNU 로 시군구를 확인하지 못했습니다. 지역별 예측값 대신 전체 기준이 사용됩니다.")

    #대표 용도지역
    main_zoning = max(area_by_zoning, key=area_by_zoning.get) if area_by_zoning else None

    return ZoneSummary(
        zoning=main_zoning,
        site_area_m2=site_area_m2,
        far_min=far_min,
        far_max=far_max,
        land_value_total=land_value_total,
        pnus=[p.pnu for p in parcels],
        warnings=warnings,
        region=region,
    )