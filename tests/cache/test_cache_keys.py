import pytest

from app.cache import keys


#키 형식이 바뀌면 운영 중인 Redis 에 저장된 캐시 · 크레딧 잔액을 읽지 못한다.
#의도적으로 바꾸는 경우에만 이 테스트를 함께 수정할 것
@pytest.mark.parametrize(
    ("spec", "parts", "expected_key", "expected_ttl"),
    [
        (keys.TRADES, ("11350", "2026-11"), "trades:11350:2026-11", 86_400),
        (keys.LAND, ("1135010500100010000",), "land:1135010500100010000", 604_800),
        (keys.BUILDING, ("P",), "bld3:P", 2_592_000),
        (keys.EXCLUSIVE_TOTAL, ("P",), "exc:P", 2_592_000),
        (keys.POSSESSION, ("P",), "own3:P", 2_592_000),
        (keys.APARTMENT, ("P",), "aptc2:P", 2_592_000),
        (keys.HOUSE_PRICE, ("P",), "hsp:P", 2_592_000),
        (keys.APARTMENT_PRICES, ("P",), "aptp:P", 2_592_000),
    ],
)
def test_cache_specs_keep_existing_keys_and_ttls(spec, parts, expected_key, expected_ttl) -> None:
    assert spec.key(*parts) == expected_key
    assert spec.ttl == expected_ttl


def test_state_keys_keep_existing_format() -> None:
    daily = keys.daily_credits(7, "2026-11-08")

    assert daily == "credits:7:2026-11-08"
    assert keys.credit_token(daily, "abc") == "credits:7:2026-11-08:token:abc"
    assert keys.purchased_credits(7) == "purchased-credits:7"
    assert keys.credit_purchase_fulfilled("order-1") == "credit-purchase-fulfilled:order-1"
    assert keys.user_lock("zone", 7) == "lock:zone:7"


def test_spec_prefixes_are_unique() -> None:
    specs = [value for value in vars(keys).values() if isinstance(value, keys.CacheSpec)]

    assert len({spec.prefix for spec in specs}) == len(specs)
