import csv
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from app.services.predict import trend

DATA_DIR = Path(__file__).parent / "data"
CASES_CSV = DATA_DIR / "sale_cases.csv"
INDEX_CSV = DATA_DIR / "sale_index.csv"
TRADES_CACHE = DATA_DIR / "trades_cache.json"
    
#전용면적 ÷ 공급면적
DEFAULT_EXCLUSIVE_RATIO = 0.74

#시세 대비 분양가 비율
DEFAULT_OFFER_TO_MARKET = 0.91

#신축 기준 준공 연도
DEFAULT_MIN_BUILD_YEAR = 2018

#슬라이더 폭
SLIDER_MARGIN = 0.15

#시점 보정 월 상승률 상한
MAX_MONTHLY_RATE = 0.007

#외삽 경고 기준
LONG_HORIZON_MONTHS = 24

#같은 시군구 최소 단지 수
MIN_REGION_COMPLEXES = 3

#사례 이름
def complex_key(name: str) -> str:
    return re.sub(r"\s+\d+[A-Za-z]?$", "", (name or "").strip())

@dataclass
class Trade:
    ym: str
    apt_name: str
    umd_nm: str
    build_year: int
    exclusive_area_m2: float
    amount_manwon: float
    deal_type: str

    @property
    def price_per_exclusive_m2(self) -> float:
        return self.amount_manwon / self.exclusive_area_m2

@dataclass
class SalePrediction:
    price_per_m2: float
    target_ym: str
    basis: str
    market_price_per_m2: float
    case_count: int
    trade_count: int
    apt_names: list[str]
    slider_min: float
    slider_max: float
    warnings: list[str] = field(default_factory=list)

@dataclass
class SaleCase:
    ym: str
    name: str
    region: str
    price_per_m2: float
    note: str = ""

#분양 사례 CSV 읽기
def load_cases(path: Path = CASES_CSV) -> list[SaleCase]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        lines = [ln for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    return [
        SaleCase(
            ym=row["ym"], name=row["name"], region=row["region"],
            price_per_m2=float(row["price_per_m2"]), note=row.get("note", ""),
        )
        for row in csv.DictReader(lines)
    ]

def _months(ym: str) -> int:
    year, month = ym.split("-")
    return int(year) * 12 + int(month)

#실거래 -> 월 상승률
def _monthly_rate(trades: list[Trade], exclusive_ratio: float) -> tuple[float, str | None]:
    picked = [t for t in trades if t.deal_type == "중개거래"]
    if len({t.ym for t in picked}) < 4:
        return 0.0, "거래 월이 적어 시점 보정을 생략했습니다."

    #월별 중앙값
    by_month: dict[str, list[float]] = {}
    for t in picked:
        by_month.setdefault(t.ym, []).append(t.price_per_exclusive_m2 * exclusive_ratio)
    series = {ym: statistics.median(v) for ym, v in by_month.items() if v}

    try:
        #받은 거래 전체 사용
        annual = trend.estimate_annual_rate(series, window_years=50).annual_rate
    except ValueError:
        return 0.0, "거래 월이 적어 시점 보정을 생략했습니다."

    rate = (1 + annual) ** (1 / 12) - 1  # 연율 -> 월율
    if abs(rate) > MAX_MONTHLY_RATE:
        capped = MAX_MONTHLY_RATE if rate > 0 else -MAX_MONTHLY_RATE
        return capped, f"실거래 추세가 월 {rate * 100:+.2f}% 로 과도해 월 {MAX_MONTHLY_RATE * 100:.1f}% 로 제한했습니다."
    return rate, None

#실거래가지수 표
def load_index(path: Path = INDEX_CSV) -> dict[str, float]:
    with open(path, encoding="utf-8") as f:
        lines = [ln for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    return {row["ym"]: float(row["index"]) for row in csv.DictReader(lines)}

#장기 분양가 상승률
def annual_rate(index: dict[str, float] | None = None) -> float:
    return trend.estimate_annual_rate(index or load_index()).annual_rate

#장기 수렴 연율
def long_run_rate() -> float:
    from app.services.predict.construction_cost import load_index as load_cost_index

    return trend.estimate_annual_rate(load_cost_index()).annual_rate

#지수로 시점 이동
def escalate(
    price: float,
    from_ym: str,
    to_ym: str,
    index: dict[str, float] | None = None,
    converge: bool = True,
) -> float:
    index = index or load_index()
    rate = trend.estimate_annual_rate(index).annual_rate
    lr = long_run_rate() if converge else None
    return (
        price
        * trend.index_at(to_ym, index, rate, long_run_rate=lr)
        / trend.index_at(from_ym, index, rate, long_run_rate=lr)
    )

#분양가 예측
def predict_sale_price_per_m2(
    target_ym: str,
    trades: list[Trade] | None = None,
    cases: list[SaleCase] | None = None,
    umd_nm: str | None = None,
    region: str | None = None,
    min_build_year: int = DEFAULT_MIN_BUILD_YEAR,
    exclusive_ratio: float = DEFAULT_EXCLUSIVE_RATIO,
    offer_to_market: float = DEFAULT_OFFER_TO_MARKET,
) -> SalePrediction:
    warnings: list[str] = []
    trades = trades if trades is not None else []
    cases = cases if cases is not None else load_cases()

    #시점 보정 월 상승률
    monthly_rate, rate_note = _monthly_rate(trades, exclusive_ratio)
    if rate_note:
        warnings.append(rate_note)

    #1순위 : 분양 사례
    if region:
        same_region = [c for c in cases if c.region == region]
        if len({complex_key(c.name) for c in same_region}) >= MIN_REGION_COMPLEXES:
            cases = same_region
        elif cases:
            warnings.append(
                f"{region} 분양 사례가 {len({complex_key(c.name) for c in same_region})}개 단지뿐이라 인접 지역 사례를 함께 사용했습니다."
            )

    if cases:
        #단지별 중앙값 -> 단지들의 중앙값
        by_complex: dict[str, list[float]] = {}
        for c in cases:
            by_complex.setdefault(complex_key(c.name), []).append(
                c.price_per_m2 * (1 + monthly_rate) ** (_months(target_ym) - _months(c.ym))
            )
        predicted = statistics.median(statistics.median(v) for v in by_complex.values())

        if len(by_complex) < 3:
            warnings.append(f"분양 사례가 {len(by_complex)}개 단지뿐이라 신뢰도가 낮습니다.")
        gap = _months(target_ym) - max(_months(c.ym) for c in cases)
        if gap > 0:
            warnings.append(f"분양 사례를 월 {monthly_rate * 100:+.2f}% 로 {target_ym} 까지 보정했습니다.")
            if gap > LONG_HORIZON_MONTHS:
                warnings.append(f"{gap}개월 차이를 보정한 값이라 오차가 큽니다. 슬라이더로 조정하세요.")

        return SalePrediction(
            price_per_m2=round(predicted, 1),
            target_ym=target_ym,
            basis="분양사례",
            market_price_per_m2=0.0,
            case_count=len(by_complex),
            trade_count=0,
            apt_names=sorted({c.name for c in cases}),
            slider_min=round(predicted * (1 - SLIDER_MARGIN), 1),
            slider_max=round(predicted * (1 + SLIDER_MARGIN), 1),
            warnings=warnings,
        )

    #2순위 : 실거래 시세 × 계수
    warnings.append("분양 사례가 없어 실거래 시세로 추정했습니다. 사례를 모으면 정확해집니다.")

    picked = [t for t in trades if t.deal_type == "중개거래"]
    new_builds = [t for t in picked if t.build_year >= min_build_year]
    if umd_nm:
        same_dong = [t for t in new_builds if t.umd_nm == umd_nm]
        if len(same_dong) >= 5:
            picked = same_dong
        elif new_builds:
            picked = new_builds
            warnings.append(f"{umd_nm} 신축 거래가 부족해 시군구 전체 신축 거래를 사용했습니다.")
        else:
            warnings.append(f"{min_build_year}년 이후 준공 거래가 없어 전체 거래를 사용했습니다.")
    elif new_builds:
        picked = new_builds

    if not picked:
        raise ValueError("분양 사례도 실거래 자료도 없습니다.")

    per_supply = [t.price_per_exclusive_m2 * exclusive_ratio for t in picked]
    xs = [_months(t.ym) for t in picked]
    x_mean, y_mean = statistics.fmean(xs), statistics.fmean(per_supply)
    market = y_mean * (1 + monthly_rate) ** (_months(target_ym) - x_mean)

    predicted = market * offer_to_market
    warnings.append(
        f"시세 대비 분양가 {offer_to_market:.2f}배 가정 (실측 사례는 1.38배였고 단지에 따라 편차가 큽니다)"
    )
    if len(picked) < 10:
        warnings.append(f"거래가 {len(picked)}건뿐이라 신뢰도가 낮습니다.")

    return SalePrediction(
        price_per_m2=round(predicted, 1),
        target_ym=target_ym,
        basis="실거래시세",
        market_price_per_m2=round(market, 1),
        case_count=0,
        trade_count=len(picked),
        apt_names=sorted({t.apt_name for t in picked}),
        slider_min=round(predicted * (1 - SLIDER_MARGIN), 1),
        slider_max=round(predicted * (1 + SLIDER_MARGIN), 1),
        warnings=warnings,
    )