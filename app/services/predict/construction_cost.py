import csv
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from app.services.predict import trend

#데이터 경로
DATA_DIR = Path(__file__).parent / "data"
INDEX_CSV = DATA_DIR / "cost_index.csv"
CASES_CSV = DATA_DIR / "cost_cases.csv"

#슬라이더 폭 (예측치 대비 ±)
SLIDER_MARGIN = 0.15

#오래된 계약 가중치 반감기 (년)
RECENCY_HALFLIFE_YEARS = 5.0

#지역 사례 단독 사용 최소 건수
REGION_MIN_CASES = 10

@dataclass
class CostCase:
    ym: str
    name: str
    region: str
    cost_per_pyeong: float
    project_type: str = ""
    note: str = ""

@dataclass
class CostPrediction:
    cost_per_pyeong: float
    target_ym: str
    index_used: float
    case_count: int
    slider_min: float
    slider_max: float
    warnings: list[str] = field(default_factory=list)

#YYYY-MM -> 개월 수
def _months(ym: str) -> int:
    year, month = ym.split("-")
    return int(year) * 12 + int(month)

#주석, 빈 줄을 건너뛰고 CSV 읽기
def _read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        lines = [ln for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    return list(csv.DictReader(lines))

def load_index(path: Path = INDEX_CSV) -> dict[str, float]:
    return {row["ym"]: float(row["index"]) for row in _read_csv(path)}

def load_cases(path: Path = CASES_CSV) -> list[CostCase]:
    return [
        CostCase(
            ym=row["ym"],
            name=row["name"],
            region=row["region"],
            cost_per_pyeong=float(row["cost_per_pyeong"]),
            project_type=row.get("project_type", ""),
            note=row.get("note", ""),
        )
        for row in _read_csv(path)
    ]

#시점 지수
def index_at(ym: str, index: dict[str, float] | None = None) -> float:
    index = index or load_index()
    return trend.index_at(ym, index)

#계약 공사비
def escalate(cost: float, from_ym: str, to_ym: str, index: dict[str, float] | None = None) -> float:
    index = index or load_index()
    rate = trend.estimate_annual_rate(index).annual_rate
    return cost * trend.index_at(to_ym, index, rate) / trend.index_at(from_ym, index, rate)

#목표 시점 평당 공사비 예측
def predict_cost_per_pyeong(
    target_ym: str,
    cases: list[CostCase] | None = None,
    region: str | None = None,
) -> CostPrediction:
    index = load_index()
    cases = cases if cases is not None else load_cases()
    warnings: list[str] = []

    #지역 사례가 충분하면 지역만, 아니면 서울 전체
    if region:
        filtered = [c for c in cases if c.region == region]
        if len(filtered) >= REGION_MIN_CASES:
            cases = filtered
        elif filtered:
            warnings.append(
                f"{region} 사례가 {len(filtered)}건뿐이라(기준 {REGION_MIN_CASES}건) "
                "서울 전체 사례로 계산했습니다. 지역 시세와 다를 수 있습니다."
            )
        else:
            warnings.append(f"{region} 사례가 없어 서울 전체 기준으로 계산했습니다.")

    if not cases:
        raise ValueError("공사비 사례가 하나도 없습니다. data/cost_cases.csv 를 확인하세요.")

    #상승률은 한 번만 구해 공통 사용
    rate_est = trend.estimate_annual_rate(index)
    warnings.extend(f"건설공사비지수 : {w}" for w in rate_est.warnings)

    escalated = [
        c.cost_per_pyeong
        * trend.index_at(target_ym, index, rate_est.annual_rate)
        / trend.index_at(c.ym, index, rate_est.annual_rate)
        for c in cases
    ]

    #최근 계약 가중 평균
    latest = max(_months(c.ym) for c in cases)
    weights = [0.5 ** ((latest - _months(c.ym)) / 12 / RECENCY_HALFLIFE_YEARS) for c in cases]
    predicted = sum(v * w for v, w in zip(escalated, weights)) / sum(weights)

    #가중 평균 범위 검사
    if not min(escalated) <= predicted <= max(escalated):
        warnings.append("가중 평균이 사례 범위를 벗어났습니다. 가중치 설정을 확인하세요.")
        predicted = median(escalated)

    if len(cases) < 5:
        warnings.append(f"사례가 {len(cases)}건뿐이라 신뢰도가 낮습니다. 사례를 더 모으세요.")

    last_ym = max(index, key=_months)
    if _months(target_ym) > _months(last_ym):
        warnings.append(
            f"지수 표의 마지막 시점({last_ym}) 이후라 연 {rate_est.annual_rate * 100:.2f}% 로 "
            "연장 추정했습니다."
        )

    #지수표 이전 계약 경고
    first_ym = min(index, key=_months)
    older = [c.ym for c in cases if _months(c.ym) < _months(first_ym)]
    if older:
        warnings.append(
            f"{len(older)}건이 지수 표 시작({first_ym})보다 이전 계약이라 상승률로 역산했습니다. "
            "지수 표를 과거까지 채우면 정확해집니다."
        )

    return CostPrediction(
        cost_per_pyeong=round(predicted, 1),
        target_ym=target_ym,
        index_used=round(index_at(target_ym, index), 2),
        case_count=len(cases),
        slider_min=round(predicted * (1 - SLIDER_MARGIN), 1),
        slider_max=round(predicted * (1 + SLIDER_MARGIN), 1),
        warnings=warnings,
    )

#계약단가의 지수 대비 초과 상승률
@dataclass
class ExcessRate:
    rate: float
    case_rate: float
    index_rate: float
    case_count: int
    basis: str
    span: str

def _loglinear_rate(points: list[tuple[float, float]]) -> float:

    #(연, 값) 목록
    import math
    n = len(points)
    xs = [x for x, _ in points]
    ys = [math.log(y) for _, y in points]
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return 0.0
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return math.exp(slope) - 1

def contract_excess_rate(
    region: str | None = None,
    cases: list[CostCase] | None = None,
    index: dict[str, float] | None = None,
) -> ExcessRate:
    cases = cases if cases is not None else load_cases()
    index = index if index is not None else load_index()

    basis = "서울 전체"
    if region:
        local = [c for c in cases if c.region == region]
        if len(local) >= REGION_MIN_CASES:
            cases, basis = local, region

    def year(ym: str) -> float:
        y, m = ym.split("-")[:2]
        return int(y) + (int(m) - 1) / 12

    if len(cases) < 3:
        return ExcessRate(0.0, 0.0, 0.0, len(cases), basis, "")

    first = min(c.ym for c in cases)
    last = max(c.ym for c in cases)
    case_rate = _loglinear_rate([(year(c.ym), c.cost_per_pyeong) for c in cases])
    window = [(year(k), v) for k, v in index.items() if first <= k <= last and v > 0]
    index_rate = _loglinear_rate(window) if len(window) >= 3 else case_rate

    return ExcessRate(
        rate=max(case_rate - index_rate, 0.0),
        case_rate=case_rate,
        index_rate=index_rate,
        case_count=len(cases),
        basis=basis,
        span=f"{first}~{last}",
    )