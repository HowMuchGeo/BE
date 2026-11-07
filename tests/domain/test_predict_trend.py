# 예측 모듈의 순수 계산 (외부 호출 없음)
import pytest

from app.services.predict import construction_cost, sale_price, trend


@pytest.mark.parametrize(("ym", "expected"), [("2026-03", 2026 * 12 + 3), ("2026", 2026 * 12 + 6)])
def test_months(ym: str, expected: int) -> None:
    #연 단위 자료는 연중(6월)으로 본다
    assert trend.months(ym) == expected


def test_escalate_compounds_annually() -> None:
    assert trend.escalate(100.0, "2020-01", "2022-01", 0.10) == pytest.approx(121.0)
    assert trend.escalate(121.0, "2022-01", "2020-01", 0.10) == pytest.approx(100.0)


def test_estimate_annual_rate_recovers_constant_growth() -> None:
    series = {f"{2006 + i}-01": 100 * 1.05 ** i for i in range(21)}

    estimate = trend.estimate_annual_rate(series)

    assert estimate.method == "logfit"
    assert estimate.annual_rate == pytest.approx(0.05, rel=1e-9)
    assert estimate.r_squared == pytest.approx(1.0)
    assert estimate.points == 21


def test_estimate_annual_rate_with_few_points_uses_cagr() -> None:
    estimate = trend.estimate_annual_rate({"2020-01": 100.0, "2022-01": 121.0})

    assert estimate.method == "cagr"
    assert estimate.annual_rate == pytest.approx(0.10)
    assert estimate.warnings


def test_estimate_annual_rate_needs_two_points() -> None:
    with pytest.raises(ValueError):
        trend.estimate_annual_rate({"2020-01": 100.0})


def test_extrapolate_without_long_run_is_plain_compound() -> None:
    assert trend.extrapolate(24, 0.10) == pytest.approx(1.21)


def test_extrapolate_converges_toward_long_run_rate() -> None:
    #장기 연율이 더 낮으면 단순 복리보다 덜 오른다
    plain = trend.extrapolate(120, 0.07)
    converged = trend.extrapolate(120, 0.07, long_run_rate=0.04)
    assert 1.04 ** 10 < converged < plain


def test_index_at_interpolates_between_points() -> None:
    index = {"2020-01": 100.0, "2020-03": 110.0}
    assert trend.index_at("2020-02", index, annual_rate=0.0) == pytest.approx(105.0)
    assert trend.index_at("2020-01", index) == 100.0


class TestBundledData:
    def test_cost_index_loads(self) -> None:
        index = construction_cost.load_index()
        assert len(index) > 100
        assert index["2020-01"] > 0

    def test_sale_index_loads(self) -> None:
        assert len(sale_price.load_index()) > 10

    def test_long_run_rate_is_cost_index_rate(self) -> None:
        expected = trend.estimate_annual_rate(construction_cost.load_index()).annual_rate
        assert sale_price.long_run_rate() == pytest.approx(expected)
        assert 0 < expected < 0.2
