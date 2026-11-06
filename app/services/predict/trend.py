from dataclasses import dataclass, field
from math import exp, log

#추정 창
WINDOW_YEARS = 20

#회귀 최소 점 개수
MIN_POINTS = 6

#자료가 창을 덮어야 하는 최소 비율
MIN_COVERAGE = 0.8


@dataclass
class RateEstimate:
    annual_rate: float
    points: int
    r_squared: float
    from_ym: str
    to_ym: str
    method: str
    warnings: list[str] = field(default_factory=list)

#YYYY-MM 또는 YYYY -> 개월 수
def months(ym: str) -> int:
    parts = str(ym).split("-")
    year = int(parts[0])
    month = int(parts[1]) if len(parts) > 1 else 6
    return year * 12 + month

#개월 수 -> YYYY-MM
def _ym(total: int) -> str:
    return f"{(total - 1) // 12}-{(total - 1) % 12 + 1:02d}"


#시계열 {YYYY-MM: 값} 의 연평균 상승률
def estimate_annual_rate(
    series: dict[str, float],
    window_years: int = WINDOW_YEARS,
    anchor_ym: str | None = None,
) -> RateEstimate:
    points = sorted((months(k), float(v)) for k, v in series.items() if float(v) > 0)
    if len(points) < 2:
        raise ValueError(f"상승률을 추정하려면 점이 2개 이상 필요합니다: {len(points)}개")

    warnings: list[str] = []
    end = months(anchor_ym) if anchor_ym else points[-1][0]
    start = end - window_years * 12

    window = [(m, v) for m, v in points if start <= m <= end]

    #창 안 자료가 모자라면 전체 사용
    if len(window) < MIN_POINTS:
        warnings.append(
            f"{window_years}년 창에 자료가 {len(window)}개뿐이라 전체 구간({_ym(points[0][0])}~"
            f"{_ym(points[-1][0])})으로 추정했습니다."
        )
        window = points

    n = len(window)
    if n < MIN_POINTS:

        #점이 적으면 두 점 CAGR
        (m0, v0), (m1, v1) = window[0], window[-1]
        years = max((m1 - m0) / 12, 1 / 12)
        rate = (v1 / v0) ** (1 / years) - 1
        warnings.append(f"점이 {n}개뿐이라 양 끝 2점으로 계산했습니다. 시작점에 민감합니다.")
        return RateEstimate(rate, n, 0.0, _ym(m0), _ym(m1), "cagr", warnings)

    #로그선형회귀 (연율 = exp(12b) − 1)
    xs = [m for m, _ in window]
    ys = [log(v) for _, v in window]
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx == 0:
        raise ValueError("시점이 모두 같아 추세를 낼 수 없습니다.")
    b = sxy / sxx
    a = my - b * mx
    rate = exp(12 * b) - 1

    #결정계수
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0

    if r2 < 0.5:
        warnings.append(f"적합도 R²={r2:.2f} 로 추세가 뚜렷하지 않습니다. 변동이 큰 구간입니다.")

    #창 덮는 비율 검사
    coverage = (xs[-1] - xs[0]) / (window_years * 12)
    if coverage < MIN_COVERAGE:
        warnings.append(
            f"자료가 {window_years}년 창의 {coverage*100:.0f}%({_ym(xs[0])}~{_ym(xs[-1])})만 "
            f"덮습니다. 구간이 짧아 단기 변동이 장기 추세로 증폭될 수 있습니다."
        )

    return RateEstimate(rate, n, r2, _ym(xs[0]), _ym(xs[-1]), "logfit", warnings)

#연율 복리로 시점 이동
def escalate(value: float, from_ym: str, to_ym: str, annual_rate: float) -> float:
    years = (months(to_ym) - months(from_ym)) / 12
    return value * (1 + annual_rate) ** years

#장기 연율 수렴 기간 (년)
CONVERGE_YEARS = 5.0

#미래 외삽 누적 배수 (long_run_rate 가 있으면 CONVERGE_YEARS 동안 수렴)
def extrapolate(months_ahead: float, annual_rate: float, long_run_rate: float | None = None) -> float:
    if months_ahead <= 0:
        return (1 + annual_rate) ** (months_ahead / 12)
    if long_run_rate is None or abs(long_run_rate - annual_rate) < 1e-12:
        return (1 + annual_rate) ** (months_ahead / 12)

    factor = 1.0
    whole = int(months_ahead)
    for m in range(whole):
        t = (m + 0.5) / 12
        g = annual_rate + (long_run_rate - annual_rate) * min(t / CONVERGE_YEARS, 1.0)
        factor *= (1 + g) ** (1 / 12)

    #남은 소수 개월
    rest = months_ahead - whole
    if rest > 0:
        t = (whole + rest / 2) / 12
        g = annual_rate + (long_run_rate - annual_rate) * min(t / CONVERGE_YEARS, 1.0)
        factor *= (1 + g) ** (rest / 12)

    return factor

def index_at(
    ym: str,
    index: dict[str, float],
    annual_rate: float | None = None,
    long_run_rate: float | None = None,
) -> float:
    key = str(ym)
    if key in index:
        return index[key]

    points = sorted((months(k), float(v)) for k, v in index.items())
    target = months(key)

    rate = annual_rate if annual_rate is not None else estimate_annual_rate(index).annual_rate

    #표 이전 : 연율로 역산
    if target <= points[0][0]:
        m_first, v_first = points[0]
        return v_first / (1 + rate) ** ((m_first - target) / 12)

    for (m0, v0), (m1, v1) in zip(points, points[1:]):
        if m0 <= target <= m1:
            return v0 + (v1 - v0) * (target - m0) / (m1 - m0)

    #표 이후 : 연율로 연장
    m_last, v_last = points[-1]
    return v_last * extrapolate(target - m_last, rate, long_run_rate)

#구간 균등 지출 시 base_ym 대비 평균 지수 배수
def average_index_ratio(
    start_ym: str,
    end_ym: str,
    base_ym: str,
    index: dict[str, float],
    annual_rate: float | None = None,
    long_run_rate: float | None = None,
    excess_rate: float = 0.0,
) -> float:
    rate = annual_rate if annual_rate is not None else estimate_annual_rate(index).annual_rate
    base = index_at(base_ym, index, rate, long_run_rate)
    m_base = months(base_ym)
    m0, m1 = months(start_ym), months(end_ym)
    if m1 <= m0:
        m1 = m0 + 1

    total = 0.0
    for m in range(m0, m1):
        t = (m + 0.5 - m_base) / 12
        total += index_at(_ym(m), index, rate, long_run_rate) / base * (1 + excess_rate) ** t
    return total / (m1 - m0)