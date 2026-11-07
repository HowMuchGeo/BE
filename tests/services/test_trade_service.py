import asyncio

import pytest

import app.clients.molit as molit
import app.services.trade_service as trade_service
from app.services.predict.sale_price import Trade
from tests.fakes import FakeRedis

ROW = {
    "dealYear": "2026", "dealMonth": "9", "aptNm": "미성", "umdNm": "월계동", "buildYear": "1986",
    "excluUseAr": "59.9", "dealAmount": "85,000", "dealingGbn": "중개거래", "cdealType": "",
}


class TestToTrade:
    def test_maps_row(self) -> None:
        assert trade_service.to_trade(ROW) == Trade(
            ym="2026-09", apt_name="미성", umd_nm="월계동", build_year=1986,
            exclusive_area_m2=59.9, amount_manwon=85000.0, deal_type="중개거래",
        )

    def test_cancelled_deal_is_skipped(self) -> None:
        assert trade_service.to_trade({**ROW, "cdealType": "O"}) is None

    @pytest.mark.parametrize("broken", [{"excluUseAr": "x"}, {"dealMonth": ""}], ids=["bad-area", "bad-month"])
    def test_malformed_row_is_skipped(self, broken: dict) -> None:
        assert trade_service.to_trade({**ROW, **broken}) is None

    def test_missing_required_key_is_skipped(self) -> None:
        row = dict(ROW)
        row.pop("dealAmount")
        assert trade_service.to_trade(row) is None

    def test_empty_build_year_is_zero(self) -> None:
        assert trade_service.to_trade({**ROW, "buildYear": ""}).build_year == 0


def test_recent_trade_months_skips_reporting_lag() -> None:
    ym_list, last_ym = trade_service.recent_trade_months("2026-11")

    #신고 기한 때문에 이번 달 · 지난달(2026-11, 2026-10)은 뺀다
    assert last_ym == "2026-09"
    assert ym_list[0] == "2026-09"
    assert ym_list[-1] == "2025-10"
    assert len(ym_list) == trade_service.TRADE_MONTHS


def test_recent_trade_months_crosses_year() -> None:
    ym_list, last_ym = trade_service.recent_trade_months("2026-02")
    assert last_ym == "2025-12"
    assert ym_list[:2] == ["2025-12", "2025-11"]


def test_fetch_trades_calls_each_month(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_get(lawd_cd, deal_ymd, service_key=None):
        calls.append((lawd_cd, deal_ymd, service_key))
        return [{**ROW, "dealMonth": deal_ymd[4:]}, {**ROW, "cdealType": "O"}]

    monkeypatch.setattr(molit, "get_apt_trades", fake_get)

    trades = trade_service.fetch_trades("11350", ["2026-09", "2026-08"])

    assert calls == [("11350", "202609", None), ("11350", "202608", None)]
    assert [t.ym for t in trades] == ["2026-09", "2026-08"]


class TestFetchRecentTrades:
    def test_fetches_and_caches(self, monkeypatch: pytest.MonkeyPatch, fake_redis: FakeRedis) -> None:
        calls = []
        monkeypatch.setattr(trade_service, "fetch_trades", lambda lawd, yms: calls.append(yms) or [trade_service.to_trade(ROW)])

        first = asyncio.run(trade_service.fetch_recent_trades("11350", "2026-11"))
        second = asyncio.run(trade_service.fetch_recent_trades("11350", "2026-11"))

        assert [t.ym for t in first] == ["2026-09"]
        assert second == first
        assert len(calls) == 1
        assert "trades:11350:2026-11" in fake_redis.store

    def test_cached_trades_after_last_month_are_dropped(self, monkeypatch: pytest.MonkeyPatch, fake_redis: FakeRedis) -> None:
        #예전 방식(목표월까지)으로 저장된 캐시에는 신고가 덜 끝난 달이 섞여 있다
        recent = trade_service.to_trade({**ROW, "dealMonth": "10"})
        older = trade_service.to_trade(ROW)
        monkeypatch.setattr(trade_service, "fetch_trades", lambda lawd, yms: [older, recent])
        asyncio.run(trade_service.fetch_recent_trades("11350", "2026-11"))

        monkeypatch.setattr(trade_service, "fetch_trades", lambda lawd, yms: pytest.fail("cache should be used"))
        cached = asyncio.run(trade_service.fetch_recent_trades("11350", "2026-11"))

        assert [t.ym for t in cached] == ["2026-09"]

    def test_failure_returns_empty_and_skips_cache(self, monkeypatch: pytest.MonkeyPatch, fake_redis: FakeRedis) -> None:
        def boom(lawd, yms):
            raise ConnectionError("down")
        monkeypatch.setattr(trade_service, "fetch_trades", boom)

        assert asyncio.run(trade_service.fetch_recent_trades("11350", "2026-11")) == []
        assert fake_redis.store == {}

    def test_empty_result_is_not_cached(self, monkeypatch: pytest.MonkeyPatch, fake_redis: FakeRedis) -> None:
        monkeypatch.setattr(trade_service, "fetch_trades", lambda lawd, yms: [])

        assert asyncio.run(trade_service.fetch_recent_trades("11350", "2026-11")) == []
        assert fake_redis.store == {}
