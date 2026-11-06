import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from app.clients import molit
from app.services.predict.sale_price import Trade
from app.services.zone_service import get_cached_trades, set_cached_trades

#백엔드 Logger
logger = logging.getLogger(__name__)

#실거래 조회 스레드풀
executor = ThreadPoolExecutor(max_workers=10)

#실거래 조회 기간
TRADE_MONTHS = 12

#실거래 신고가 끝난 달만 사용
REPORTING_LAG_MONTHS = 2

#원본 행
def to_trade(row: dict) -> Trade | None:

    #해제된 거래는 제외 (cdealType 에 '해제' 가 들어온다)
    if row.get("cdealType"):
        return None
    try:
        return Trade(
            ym=f"{row['dealYear']}-{int(row['dealMonth']):02d}",
            apt_name=row.get("aptNm", ""),
            umd_nm=row.get("umdNm", ""),
            build_year=int(row.get("buildYear") or 0),
            exclusive_area_m2=float(row["excluUseAr"]),
            amount_manwon=float(row["dealAmount"].replace(",", "")),
            deal_type=row.get("dealingGbn", ""),
        )
    except (KeyError, ValueError):
        return None

#국토부 실거래가 조회
def fetch_trades(lawd_cd: str, ym_list: list[str], service_key: str | None = None) -> list[Trade]:
    trades: list[Trade] = []
    for ym in ym_list:
        for row in molit.get_apt_trades(lawd_cd, ym.replace("-", ""), service_key):
            trade = to_trade(row)
            if trade is not None:
                trades.append(trade)
    return trades

#신고가 끝난 달부터 거슬러 TRADE_MONTHS 개월
def recent_trade_months(target_ym: str) -> tuple[list[str], str]:
    year, month = (int(v) for v in target_ym.split("-"))
    last = year * 12 + (month - 1) - REPORTING_LAG_MONTHS
    last_ym = f"{last // 12}-{last % 12 + 1:02d}"
    ym_list = []
    for step in range(TRADE_MONTHS):
        total = last - step
        ym_list.append(f"{total // 12}-{total % 12 + 1:02d}")
    return ym_list, last_ym

#분양가 시점 보정용 실거래 목록
async def fetch_recent_trades(lawd_cd: str, target_ym: str) -> list:
    ym_list, last_ym = recent_trade_months(target_ym)

    #Redis 캐시 조회
    cached = await get_cached_trades(lawd_cd, target_ym)
    if cached is not None:
        return [t for t in cached if t.ym <= last_ym]

    try:
        loop = asyncio.get_running_loop()
        trades = await loop.run_in_executor(executor, fetch_trades, lawd_cd, ym_list)
    except Exception as err:
        logger.warning(f"[ Log ] : 실거래가 조회 실패 {lawd_cd} : {err}")
        return []

    #호출 및 trades 데이터 반환 성공시, Redis 캐싱 후 값 반환
    if trades:
        await set_cached_trades(lawd_cd, target_ym, trades)

    return trades