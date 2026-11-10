#Redis 키 · TTL 정의 모음
#키 형식을 바꾸면 기존에 저장된 값을 읽지 못한다. 캐시 구조가 바뀔 때만 prefix 버전을 올린다 (예: bld3, own3, aptc2)
from dataclasses import dataclass

#TTL (초)
DAY = 86_400
WEEK = 7 * DAY
MONTH = 30 * DAY

#캐시 종류별 키 prefix · TTL · 로그 라벨
@dataclass(frozen=True)
class CacheSpec:
    prefix: str
    ttl: int
    label: str

    #prefix:part1:part2 형태의 키 생성
    def key(self, *parts: object) -> str:
        return ":".join((self.prefix, *(str(part) for part in parts)))

#=== 외부 API 조회 결과 캐시 ===#
#국토부 실거래가 (trades:{lawd_cd}:{target_ym})
TRADES = CacheSpec("trades", DAY, "Trade")

#V-World 토지 정보 (land:{pnu})
LAND = CacheSpec("land", WEEK, "Land")

#건축물대장 (bld3:{pnu})
BUILDING = CacheSpec("bld3", MONTH, "Building")

#전유면적 합계 (exc:{pnu})
EXCLUSIVE_TOTAL = CacheSpec("exc", MONTH, "Exclusive")

#토지소유정보 (own3:{pnu})
POSSESSION = CacheSpec("own3", MONTH, "Possession")

#공동주택 단지 정보. aptc2 : 상가(비주거 동) 호수·연면적을 더한 판 (aptc2:{pnu})
APARTMENT = CacheSpec("aptc2", MONTH, "Apartment")

#개별주택가격 (단독·다가구) — 재개발 종전자산 (hsp:{pnu})
HOUSE_PRICE = CacheSpec("hsp", MONTH, "HousePrice")

#공동주택가격 (aptp:{pnu})
APARTMENT_PRICES = CacheSpec("aptp", MONTH, "ApartmentPrice")

#=== 크레딧 ===#
#일일 무료 크레딧 잔액 (자정 만료)
def daily_credits(user_id: int, date: str) -> str:
    return f"credits:{user_id}:{date}"

#계산 1회분 차감 토큰 (일일 크레딧 키에 종속)
def credit_token(daily_key: str, token: str) -> str:
    return f"{daily_key}:token:{token}"

#결제로 충전한 크레딧 잔액 (만료 없음)
def purchased_credits(user_id: int) -> str:
    return f"purchased-credits:{user_id}"

#크레딧 결제 지급 완료 표시 (중복 지급 방지)
def credit_purchase_fulfilled(partner_order_id: str) -> str:
    return f"credit-purchase-fulfilled:{partner_order_id}"

#=== 요청 잠금 ===#
#사용자별 동시 실행 1건 제한
def user_lock(name: str, user_id: int) -> str:
    return f"lock:{name}:{user_id}"
