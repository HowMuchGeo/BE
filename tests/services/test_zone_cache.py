import asyncio
import json

import pytest

import app.services.zone_service as zone_service
from app.services.predict.sale_price import Trade
from tests.fakes import BrokenRedis, FakeRedis

TRADE = Trade(
    ym="2026-09",
    apt_name="가락쌍용",
    umd_nm="가락동",
    build_year=1997,
    exclusive_area_m2=84.9,
    amount_manwon=150000.0,
    deal_type="중개거래",
)

PNU = "1171010700100010000"


class TestTrades:
    def test_roundtrip_restores_trade_objects(self, fake_redis: FakeRedis) -> None:
        asyncio.run(zone_service.set_cached_trades("11710", "2026-11", [TRADE]))

        assert fake_redis.ttl["trades:11710:2026-11"] == 86_400
        assert asyncio.run(zone_service.get_cached_trades("11710", "2026-11")) == [TRADE]

    def test_empty_list_is_not_cached(self, fake_redis: FakeRedis) -> None:
        asyncio.run(zone_service.set_cached_trades("11710", "2026-11", []))

        assert fake_redis.store == {}

    def test_stale_schema_is_treated_as_miss(self, fake_redis: FakeRedis) -> None:
        fake_redis.store["trades:11710:2026-11"] = json.dumps([{"ym": "2026-09", "removed_field": 1}])

        assert asyncio.run(zone_service.get_cached_trades("11710", "2026-11")) is None


class TestExclusiveTotal:
    def test_roundtrip_as_float(self, fake_redis: FakeRedis) -> None:
        asyncio.run(zone_service.set_cached_exclusive_total(PNU, 1234.5))

        assert fake_redis.store[f"exc:{PNU}"] == "1234.5"
        assert asyncio.run(zone_service.get_cached_exclusive_total(PNU)) == 1234.5

    def test_integer_value_is_returned_as_float(self, fake_redis: FakeRedis) -> None:
        fake_redis.store[f"exc:{PNU}"] = "1200"

        result = asyncio.run(zone_service.get_cached_exclusive_total(PNU))

        assert result == 1200.0 and isinstance(result, float)

    def test_zero_is_not_cached(self, fake_redis: FakeRedis) -> None:
        asyncio.run(zone_service.set_cached_exclusive_total(PNU, 0))

        assert fake_redis.store == {}


#dict 캐시 : (getter, setter, 키 prefix, TTL)
DICT_CACHES = [
    pytest.param(zone_service.get_cached_land, zone_service.set_cached_land, "land", 604_800, id="land"),
    pytest.param(zone_service.get_cached_building, zone_service.set_cached_building, "bld3", 2_592_000, id="building"),
    pytest.param(zone_service.get_cached_possession, zone_service.set_cached_possession, "own3", 2_592_000, id="possession"),
    pytest.param(zone_service.get_cached_apartment, zone_service.set_cached_apartment, "aptc2", 2_592_000, id="apartment"),
    pytest.param(zone_service.get_cached_house_price, zone_service.set_cached_house_price, "hsp", 2_592_000, id="house-price"),
    pytest.param(
        zone_service.get_cached_apartment_prices, zone_service.set_cached_apartment_prices, "aptp", 2_592_000,
        id="apartment-prices",
    ),
]


@pytest.mark.parametrize(("getter", "setter", "prefix", "ttl"), DICT_CACHES)
class TestDictCaches:
    def test_roundtrip(self, fake_redis: FakeRedis, getter, setter, prefix: str, ttl: int) -> None:
        data = {"pnu": PNU, "주용도": "공동주택", "values": [1, 2]}

        asyncio.run(setter(PNU, data))

        assert fake_redis.ttl[f"{prefix}:{PNU}"] == ttl
        assert asyncio.run(getter(PNU)) == data

    def test_empty_dict_is_not_cached(self, fake_redis: FakeRedis, getter, setter, prefix: str, ttl: int) -> None:
        asyncio.run(setter(PNU, {}))

        assert fake_redis.store == {}

    def test_redis_down_falls_back_silently(
        self, monkeypatch: pytest.MonkeyPatch, getter, setter, prefix: str, ttl: int
    ) -> None:
        from app.cache.store import redis_store

        monkeypatch.setattr(redis_store, "_client", BrokenRedis())

        asyncio.run(setter(PNU, {"a": 1}))
        assert asyncio.run(getter(PNU)) is None
