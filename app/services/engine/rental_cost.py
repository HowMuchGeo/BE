import re
from datetime import date
from app.services.engine import policy

_RULE = policy.RULES["rental_takeover"]

#인수 비율
TAKEOVER_RATIO = float(_RULE["ratio"])

#가산비 비율
SURCHARGE_RATIO = float(_RULE.get("surcharge", 0.0))

#지하층건축비
BASEMENT_BUILD_COST_KRW_THOUSAND = float(_RULE["basement"])

#고시 정보
NOTICE = str(_RULE["notice"])
ANNOUNCED = str(_RULE["announced"])

#고시 월 (YYYY-MM)
ANNOUNCED_YM = ANNOUNCED[:7]

#주거전용면적 구간 상한
EXCLUSIVE_UPPERS_M2 = [None if u is None else float(u) for u in _RULE["exclusive_upper_m2"]]

#층수 구간별 지상층건축비
BASE_BUILD_COST_KRW_THOUSAND = {
    band: [float(v) for v in row] for band, row in _RULE["table"].items()
}

#층수 구간 목록
FLOOR_BANDS = list(BASE_BUILD_COST_KRW_THOUSAND)

#기본 층수 구간
DEFAULT_FLOOR_BAND = "16~25층"

#예전 구간 이름 -> 새 구간
LEGACY_FLOOR_BANDS = {"11~20층": DEFAULT_FLOOR_BAND, "21층 이상": DEFAULT_FLOOR_BAND}

#규칙 표 형식 검사
def _check_table() -> None:
    width = len(EXCLUSIVE_UPPERS_M2)
    for band, row in BASE_BUILD_COST_KRW_THOUSAND.items():
        if len(row) != width:
            raise ValueError(
                f"policy_rules.json rental_takeover.table[{band}] 칸 수({len(row)})가 "
                f"전용면적 구간 수({width})와 다릅니다"
            )
    if DEFAULT_FLOOR_BAND not in BASE_BUILD_COST_KRW_THOUSAND:
        raise ValueError(f"기본 층수 구간 {DEFAULT_FLOOR_BAND} 이 rental_takeover.table 에 없습니다")

_check_table()

#기본형건축비
def base_build_cost_per_m2(floor_band: str, exclusive_area_m2: float) -> float:
    row = BASE_BUILD_COST_KRW_THOUSAND.get(LEGACY_FLOOR_BANDS.get(floor_band, floor_band))
    if row is None:
        raise ValueError(
            f"층수 구간이 올바르지 않습니다: {floor_band} (가능한 값: {', '.join(FLOOR_BANDS)})"
        )

    for upper, cost in zip(EXCLUSIVE_UPPERS_M2, row):
        if upper is None or exclusive_area_m2 <= upper:
            return cost / 10  # 천원 → 만원
    return row[-1] / 10

#의무 임대 지상층 인수가격
def takeover_price_per_m2(floor_band: str, exclusive_area_m2: float) -> float:
    return base_build_cost_per_m2(floor_band, exclusive_area_m2) * (TAKEOVER_RATIO + SURCHARGE_RATIO)

#의무 임대 지하층 인수가격
def takeover_basement_price_per_m2() -> float:
    return BASEMENT_BUILD_COST_KRW_THOUSAND / 10 * (TAKEOVER_RATIO + SURCHARGE_RATIO)

_STD = policy.RULES["standard_build_cost"]

#완화분 단가 기준
UPLIFT_BASIS = str(policy.RULES["uplift_takeover_basis"])
UPLIFT_RATIO = float(policy.RULES["uplift_takeover_ratio"])
if UPLIFT_BASIS not in ("표준건축비", "기본형건축비"):
    raise ValueError(f"policy_rules.json uplift_takeover_basis 가 올바르지 않습니다: {UPLIFT_BASIS}")

STANDARD_NOTICE = str(_STD["notice"])
STANDARD_ANNOUNCED = str(_STD["announced"])
STANDARD_ANNOUNCED_YM = STANDARD_ANNOUNCED[:7]
STANDARD_BASEMENT_RATIO = float(_STD["basement_ratio"])
STANDARD_EXCLUSIVE_UPPERS_M2 = [None if u is None else float(u) for u in _STD["exclusive_upper_m2"]]
STANDARD_FLOOR_UPPERS = [None if u is None else int(u) for u in _STD["floor_upper"]]
STANDARD_BUILD_COST_KRW_THOUSAND = {band: [float(v) for v in row] for band, row in _STD["table"].items()}
STANDARD_FLOOR_BANDS = list(STANDARD_BUILD_COST_KRW_THOUSAND)

#표준건축비 연 상승률
def _standard_annual_rate() -> float:
    start = date.fromisoformat(str(_STD["previous_announced"]))
    end = date.fromisoformat(STANDARD_ANNOUNCED)
    years = (end - start).days / 365.25
    return (1 + float(_STD["revision_increase"])) ** (1 / years) - 1

STANDARD_ANNUAL_RATE = _standard_annual_rate()

def _check_standard_table() -> None:
    if len(STANDARD_FLOOR_UPPERS) != len(STANDARD_FLOOR_BANDS):
        raise ValueError("policy_rules.json standard_build_cost.floor_upper 와 table 행 수가 다릅니다")
    for band, row in STANDARD_BUILD_COST_KRW_THOUSAND.items():
        if len(row) != len(STANDARD_EXCLUSIVE_UPPERS_M2):
            raise ValueError(f"policy_rules.json standard_build_cost.table[{band}] 칸 수가 전용면적 구간 수와 다릅니다")

_check_standard_table()

#기본형건축비 층수 구간 -> 표준건축비 층수 구간
def standard_floor_band(floor_band: str) -> str:
    band = LEGACY_FLOOR_BANDS.get(floor_band, floor_band)
    if band not in BASE_BUILD_COST_KRW_THOUSAND:
        raise ValueError(f"층수 구간이 올바르지 않습니다: {floor_band} (가능한 값: {', '.join(FLOOR_BANDS)})")
    top = int(re.findall(r"\d+", band)[-1])
    for name, upper in zip(STANDARD_FLOOR_BANDS, STANDARD_FLOOR_UPPERS):
        if upper is None or top <= upper:
            return name
    return STANDARD_FLOOR_BANDS[-1]

#표준건축비
def standard_build_cost_per_m2(floor_band: str, exclusive_area_m2: float) -> float:
    row = STANDARD_BUILD_COST_KRW_THOUSAND[standard_floor_band(floor_band)]
    for upper, cost in zip(STANDARD_EXCLUSIVE_UPPERS_M2, row):
        if upper is None or exclusive_area_m2 <= upper:
            return cost / 10
    return row[-1] / 10

#완화분 지상층 인수가격
def uplift_takeover_price_per_m2(floor_band: str, exclusive_area_m2: float) -> float:
    if UPLIFT_BASIS == "기본형건축비":
        return base_build_cost_per_m2(floor_band, exclusive_area_m2) * UPLIFT_RATIO
    return standard_build_cost_per_m2(floor_band, exclusive_area_m2) * UPLIFT_RATIO

#완화분 지하층 인수가격
def uplift_takeover_basement_price_per_m2(floor_band: str, exclusive_area_m2: float) -> float:
    if UPLIFT_BASIS == "기본형건축비":
        return BASEMENT_BUILD_COST_KRW_THOUSAND / 10 * UPLIFT_RATIO
    return standard_build_cost_per_m2(floor_band, exclusive_area_m2) * STANDARD_BASEMENT_RATIO * UPLIFT_RATIO

#완화분 고시 월, 근거
UPLIFT_ANNOUNCED_YM = ANNOUNCED_YM if UPLIFT_BASIS == "기본형건축비" else STANDARD_ANNOUNCED_YM
UPLIFT_NOTICE = NOTICE if UPLIFT_BASIS == "기본형건축비" else STANDARD_NOTICE