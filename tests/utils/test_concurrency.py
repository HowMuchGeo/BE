import asyncio

import pytest

from app.utils.concurrency import bounded_gather


def test_bounded_gather_limits_in_flight_and_keeps_order():
    in_flight = 0
    peak = 0

    async def job(i: int) -> int:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        in_flight -= 1
        return i * 10

    result = asyncio.run(bounded_gather((job(i) for i in range(10)), limit=2))

    assert result == [i * 10 for i in range(10)]
    assert peak == 2


def test_bounded_gather_empty():
    assert asyncio.run(bounded_gather([], limit=2)) == []


def test_bounded_gather_rejects_invalid_limit():
    with pytest.raises(ValueError):
        asyncio.run(bounded_gather([], limit=0))
