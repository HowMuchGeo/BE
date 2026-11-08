import asyncio
from collections.abc import Awaitable, Iterable
from typing import TypeVar

T = TypeVar("T")


#동시에 limit 개까지만 실행하는 gather.
#외부 API 작업 대기열(ThreadPoolExecutor)은 모든 사용자가 공유하므로,
#요청 하나가 필지 N개를 한꺼번에 넣으면 다른 사용자의 조회가 그 뒤에 줄을 선다
async def bounded_gather(aws: Iterable[Awaitable[T]], limit: int) -> list[T]:
    if limit < 1:
        raise ValueError("limit 은 1 이상이어야 합니다.")

    semaphore = asyncio.Semaphore(limit)

    async def run(aw: Awaitable[T]) -> T:
        async with semaphore:
            return await aw

    return await asyncio.gather(*(run(aw) for aw in aws))
