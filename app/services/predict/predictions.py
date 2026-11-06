from dataclasses import dataclass
from datetime import date
from app.services.predict.construction_cost import CostPrediction, predict_cost_per_pyeong
from app.services.predict.sale_price import SalePrediction, Trade, predict_sale_price_per_m2

#실거래 조회 기간
TRADE_MONTHS = 12

@dataclass
class Predictions:
    target_ym: str
    cost: CostPrediction
    sale: SalePrediction

    @property
    def warnings(self) -> list[str]:
        return (
            [f"[공사비] {w}" for w in self.cost.warnings]
            + [f"[분양가] {w}" for w in self.sale.warnings]
        )

#현재 연월 (YYYY-MM)
def current_ym() -> str:
    today = date.today()
    return f"{today.year}-{today.month:02d}"

#target_ym 직전 TRADE_MONTHS 개월 목록
def _recent_yms(target_ym: str, months: int = TRADE_MONTHS) -> list[str]:
    year, month = (int(x) for x in target_ym.split("-"))
    end = year * 12 + month

    #미래 시점이면 오늘까지만
    year_now, month_now = (int(x) for x in current_ym().split("-"))
    end = min(end, year_now * 12 + month_now)
    return [f"{(end - i - 1) // 12}-{(end - i - 1) % 12 + 1:02d}" for i in reversed(range(months))]

#공사비, 분양가 예측
def predict_all(
    target_ym: str | None = None,
    region: str | None = None,
    trades: list[Trade] | None = None,
) -> Predictions:
    target_ym = target_ym or current_ym()

    return Predictions(
        target_ym=target_ym,
        cost=predict_cost_per_pyeong(target_ym, region=region),
        sale=predict_sale_price_per_m2(target_ym, trades=trades or [], region=region),
    )