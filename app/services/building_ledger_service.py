import logging
from dataclasses import dataclass, field
from datetime import date
from app.clients import building_hub
from app.config.settings import settings

logger = logging.getLogger(__name__)

#부속지번 최대 페이지 개수
MAX_ATCH_PAGES = 5

#표제부 최대 페이지 개수
MAX_TITLE_PAGES = 5

#전유공용 최대 페이지 개수
MAX_EXPOS_PAGES = 50

@dataclass
class BuildingLedger:
    pnu: str
    floor_area_m2: float = 0.0
    structure: str = ""
    approval_ymd: str = ""
    elapsed_years: float = 0.0
    household_count: int = 1
    is_condo: bool = False
    main_purpose: str = ""
    etc_purpose: str = ""
    floors: int = 0
    has_building: bool = False
    fallback: bool = False
    warnings: list[str] = field(default_factory=list)

def _service_key() -> str | None:

    #없으면 건물분 없이(나대지로) 계산된다
    return settings.bldrgst_api_key

#PNU 처리
def parse_pnu(pnu: str) -> tuple[str, str, str, str, str] | None:
    pnu = (pnu or "").strip()
    if len(pnu) != 19 or not pnu.isdigit():
        return None
    return (
        pnu[0:5],                       # sigunguCd
        pnu[5:10],                      # bjdongCd
        "1" if pnu[10] == "2" else "0",  # platGbCd : 산이면 1
        pnu[11:15],                     # bun
        pnu[15:19],                     # ji
    )

def _f(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def _i(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default

#사용승인일
def elapsed_years(approval_ymd: str, as_of: date | None = None) -> float:
    if not approval_ymd or len(approval_ymd) < 4:
        return 0.0
    as_of = as_of or date.today()
    try:
        year = int(approval_ymd[0:4])
        month = int(approval_ymd[4:6]) if len(approval_ymd) >= 6 else 1
    except ValueError:
        return 0.0
    years = (as_of.year - year) + (as_of.month - max(month, 1)) / 12.0
    return max(years, 0.0)

#필지 1건의 건축물대장 요약
def fetch_building(pnu: str, as_of: date | None = None) -> BuildingLedger:
    result = BuildingLedger(pnu=pnu)
    parts = parse_pnu(pnu)
    if parts is None:
        result.fallback = True
        result.warnings.append(f"PNU 형식이 아닙니다: {pnu}")
        return result

    key = _service_key()
    if not key:
        result.fallback = True
        result.warnings.append("건축물대장 API 키(BLDRGST_API_KEY)가 없어 건물분을 계산하지 못했습니다.")
        return result

    sigungu, bjdong, plat_gb, bun, ji = parts
    base = {"sigunguCd": sigungu, "bjdongCd": bjdong, "platGbCd": plat_gb, "bun": bun, "ji": ji}

    try:
        titles, _ = building_hub.get_titles(base, MAX_TITLE_PAGES)
    except Exception as err:
        logger.warning(f"[ Log ] : 건축물대장 표제부 조회 실패 {pnu} : {err}")
        result.fallback = True
        result.warnings.append("건축물대장을 조회하지 못해 건물분을 0 으로 두었습니다.")
        return result

    #표제부가 없으면 나대지
    if not titles:
        return result

    result.has_building = True
    result.floor_area_m2 = sum(_f(row.get("totArea")) for row in titles)
    housing_rows = [row for row in titles if _i(row.get("hhldCnt")) > 0 or _i(row.get("fmlyCnt")) > 0]
    main = max(housing_rows or titles, key=lambda row: _f(row.get("totArea")))
    result.structure = (main.get("strctCdNm") or "").strip()
    result.main_purpose = (main.get("mainPurpsCdNm") or "").strip()
    result.etc_purpose = (main.get("etcPurps") or "").strip()
    result.floors = _i(main.get("grndFlrCnt"))
    result.approval_ymd = (main.get("useAprDay") or "").strip()
    result.elapsed_years = elapsed_years(result.approval_ymd, as_of)

    #집합건물 여부
    kinds = " ".join((row.get("regstrKindCdNm") or "") for row in titles)
    hhld = sum(_i(row.get("hhldCnt")) for row in titles)
    fmly = sum(_i(row.get("fmlyCnt")) for row in titles)

    #단독주택
    result.household_count = max(hhld, fmly, 1)
    result.is_condo = "집합" in kinds or result.household_count > 1

    return result

#집합건물 전유면적 합계
def fetch_exclusive_total(pnu: str) -> float:
    parts = parse_pnu(pnu)
    key = _service_key()
    if parts is None or not key:
        return 0.0

    sigungu, bjdong, plat_gb, bun, ji = parts
    base = {"sigunguCd": sigungu, "bjdongCd": bjdong, "platGbCd": plat_gb, "bun": bun, "ji": ji}

    try:
        rows, total = building_hub.get_expos_pubuse_areas(base, MAX_EXPOS_PAGES)
    except Exception as err:
        logger.warning(f"[ Log ] : 건축물대장 전유공용 조회 실패 {pnu} : {err}")
        return 0.0

    if total > len(rows):
        logger.warning(f"[ Log ] : 전유공용 {total}행 중 {len(rows)}행만 수신 {pnu} — 합계 포기")
        return 0.0

    return sum(
        _f(row.get("area")) for row in rows
        if "전유" in (row.get("exposPubuseGbCdNm") or "")
        and "부속" not in (row.get("mainAtchGbCdNm") or "")
    )

#아파트 단지 정보
@dataclass
class ApartmentComplex:
    pnu: str
    households: int = 0
    far_area_m2: float = 0.0
    annex_pnus: list[str] = field(default_factory=list)
    recap_found: bool = False

    #상가 등 비주거 건물
    commercial_units: int = 0
    commercial_floor_area_m2: float = 0.0
    commercial_structure: str = ""
    commercial_approval_ymd: str = ""
    total_floor_area_m2: float = 0.0
    fallback: bool = False

def fetch_apartment_complex(pnu: str) -> ApartmentComplex:
    result = ApartmentComplex(pnu=pnu)
    parts = parse_pnu(pnu)
    key = _service_key()
    if parts is None or not key:
        result.fallback = True
        return result

    sigungu, bjdong, plat_gb, bun, ji = parts
    base = {"sigunguCd": sigungu, "bjdongCd": bjdong, "platGbCd": plat_gb, "bun": bun, "ji": ji}

    try:
        recaps, _ = building_hub.get_recap_titles(base, 1)
        titles, _ = building_hub.get_titles(base, MAX_TITLE_PAGES)
        if recaps:
            result.recap_found = True
            result.households = sum(_i(row.get("hhldCnt")) for row in recaps)
            result.far_area_m2 = max(_f(row.get("vlRatEstmTotArea")) for row in recaps)
        else:
            result.households = sum(_i(row.get("hhldCnt")) for row in titles if "공동주택" in (row.get("mainPurpsCdNm") or ""))
            result.far_area_m2 = sum(_f(row.get("vlRatEstmTotArea")) for row in titles)

        result.total_floor_area_m2 = sum(_f(row.get("totArea")) for row in titles)
        shops = [row for row in titles if "공동주택" not in (row.get("mainPurpsCdNm") or "") and _f(row.get("totArea")) > 0]
        if shops:
            result.commercial_units = sum(_i(row.get("hoCnt")) or _i(row.get("hhldCnt")) or 1 for row in shops)
            result.commercial_floor_area_m2 = sum(_f(row.get("totArea")) for row in shops)
            main_shop = max(shops, key=lambda row: _f(row.get("totArea")))
            result.commercial_structure = (main_shop.get("strctCdNm") or "").strip()
            result.commercial_approval_ymd = (main_shop.get("useAprDay") or "").strip()

        atch, _ = building_hub.get_atch_jibuns(base, MAX_ATCH_PAGES)
        annex = []
        for row in atch:
            a_bun, a_ji = (row.get("atchBun") or "").strip(), (row.get("atchJi") or "").strip()
            if not a_bun.isdigit():
                continue
            a_sigungu = (row.get("atchSigunguCd") or sigungu).strip() or sigungu
            a_bjdong = (row.get("atchBjdongCd") or bjdong).strip() or bjdong
            a_plat = "2" if (row.get("atchPlatGbCd") or "0").strip() == "1" else "1"
            annex_pnu = f"{a_sigungu}{a_bjdong}{a_plat}{int(a_bun):04d}{int(a_ji or 0):04d}"
            if annex_pnu != pnu and annex_pnu not in annex:
                annex.append(annex_pnu)
        result.annex_pnus = annex
    except Exception as err:
        logger.warning(f"[ Log ] : 건축물대장 총괄표제부·부속지번 조회 실패 {pnu} : {err}")
        result.fallback = True
    return result