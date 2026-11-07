import asyncio
from datetime import datetime

import pytest

import app.clients.vworld as vworld
import app.services.land_service as land
from app.services.building_ledger_service import ApartmentComplex, BuildingLedger
from tests.fakes import FakeRedis, FakeResponse, FakeSession

PNU = "1135010200100170000"
THIS_YEAR = datetime.now().year


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def vworld_rows(monkeypatch: pytest.MonkeyPatch):
    """엔드포인트별 응답(dict)을 등록하면 vworld._SESSION 이 그대로 돌려준다.

    값이 callable 이면 params 를 받아 응답 dict 를 만든다. Exception 이면 그 예외를 던진다
    """
    routes: dict = {}

    def respond(url: str, params: dict) -> FakeResponse:
        route = routes[url.rsplit("/", 1)[1]]
        if isinstance(route, Exception):
            raise route
        return FakeResponse(route(params) if callable(route) else route)

    session = FakeSession(respond)
    monkeypatch.setattr(vworld, "_SESSION", session)
    routes["_session"] = session
    return routes


#=== 개별공시지가 ===#
class TestLandPrice:
    def test_uses_latest_year(self, vworld_rows) -> None:
        vworld_rows["getIndvdLandPrice"] = {"indvdLandPrices": {"field": [
            {"stdrYear": "2024", "pblntfPclnd": "1000"},
            {"stdrYear": "2025", "pblntfPclnd": "1200"},
        ]}}
        assert run(land.fetch_land_price_per_m2(PNU)) == 1200

    def test_no_rows_returns_zero(self, vworld_rows) -> None:
        vworld_rows["getIndvdLandPrice"] = {"indvdLandPrices": {"field": []}}
        assert run(land.fetch_land_price_per_m2(PNU)) == 0

    def test_error_returns_zero(self, vworld_rows) -> None:
        vworld_rows["getIndvdLandPrice"] = ConnectionError("down")
        assert run(land.fetch_land_price_per_m2(PNU)) == 0


#=== 토지특성 ===#
class TestLandCharacteristics:
    def test_maps_latest_row_and_caches(self, vworld_rows, fake_redis: FakeRedis) -> None:
        vworld_rows["getLandCharacteristics"] = {"landCharacteristicss": {"field": [
            {"stdrYear": "2023", "lndpclAr": "10"},
            {"stdrYear": "2025", "lndpclAr": "123.4", "prposArea1Nm": "제3종일반주거지역",
             "lndcgrCodeNm": "대", "pblntfPclnd": "5000.0"},
        ]}}

        result = run(land.fetch_land_characteristics(PNU))

        assert result == {
            "area_m2": 123.4,
            "zoning": "제3종일반주거지역",
            "land_category": "대",
            "land_price_per_m2": 5000,
            "fallback": False,
        }
        #두 번째 호출은 캐시에서 → 외부 호출이 늘지 않는다
        calls = len(vworld_rows["_session"].calls)
        assert run(land.fetch_land_characteristics(PNU)) == result
        assert len(vworld_rows["_session"].calls) == calls

    def test_missing_fields_fall_back_to_codes_and_defaults(self, vworld_rows, fake_redis: FakeRedis) -> None:
        vworld_rows["getLandCharacteristics"] = {"landCharacteristicss": {"field": [
            {"stdrYear": "2025", "prposArea1Cd": "UQA123", "lndcgrCode": "08"},
        ]}}

        result = run(land.fetch_land_characteristics(PNU))

        assert result["area_m2"] == land.LAND_DEFAULT["area_m2"]
        assert result["zoning"] == "UQA123"
        assert result["land_category"] == "08"
        assert result["land_price_per_m2"] == 0
        assert result["fallback"] is False

    def test_no_rows_returns_default_without_cache(self, vworld_rows, fake_redis: FakeRedis) -> None:
        vworld_rows["getLandCharacteristics"] = {"landCharacteristicss": {"field": []}}

        assert run(land.fetch_land_characteristics(PNU)) == land.LAND_DEFAULT
        assert fake_redis.store == {}

    def test_error_returns_default_copy(self, vworld_rows, fake_redis: FakeRedis) -> None:
        vworld_rows["getLandCharacteristics"] = TimeoutError("slow")

        result = run(land.fetch_land_characteristics(PNU))

        assert result == land.LAND_DEFAULT
        #기본값 원본이 바뀌지 않도록 복사본을 돌려준다
        result["area_m2"] = 1.0
        assert land.LAND_DEFAULT["area_m2"] == 300.0


#=== 토지소유정보 ===#
class TestPossessionSummary:
    def test_private_majority_wins_over_first_public_row(self) -> None:
        #첫 행이 시·도유지라도 사유 지분이 섞여 있으면 가장 많은 사유 구분을 쓴다 (월계동 17 실측)
        rows = [
            {"posesnSeCodeNm": "시, 도유지", "posesnSeCode": "05"},
            {"posesnSeCodeNm": "개인", "posesnSeCode": "01"},
            {"posesnSeCodeNm": "개인", "posesnSeCode": "01"},
            {"posesnSeCodeNm": "법인", "posesnSeCode": "03"},
        ]
        result = land._summarize_possessions(rows, total=4679)

        assert (result["owner_type"], result["owner_code"]) == ("개인", "01")
        assert result["owner_count"] == 4679
        assert result["fallback"] is False

    def test_all_public_stays_public(self) -> None:
        rows = [{"posesnSeCodeNm": "국유지", "posesnSeCode": "02"}] * 3
        result = land._summarize_possessions(rows, total=None)

        assert result["owner_type"] == "국유지"
        assert result["owner_count"] == 3

    def test_counts_owners_before_1997(self) -> None:
        rows = [
            {"posesnSeCodeNm": "개인", "ownshipChgDe": "1990-05-01"},
            {"posesnSeCodeNm": "개인", "ownshipChgDe": "1997-01-14"},
            {"posesnSeCodeNm": "개인", "ownshipChgDe": "1997-01-15"},
            {"posesnSeCodeNm": "개인", "ownshipChgDe": ""},
        ]
        assert land._summarize_possessions(rows, None)["owners_before_1997"] == 2

    def test_empty_rows_is_fallback(self) -> None:
        assert land._summarize_possessions([], None) == land.POSSESSION_EMPTY


class TestFetchPossession:
    def test_requests_sample_rows_and_caches(self, vworld_rows, fake_redis: FakeRedis) -> None:
        vworld_rows["getPossessionAttr"] = {"possessions": {"totalCount": "1", "field": [{"posesnSeCodeNm": "개인"}]}}

        result = run(land.fetch_land_possession(PNU))

        assert result["owner_type"] == "개인"
        assert vworld_rows["_session"].calls[0]["params"]["numOfRows"] == land.POSSESSION_SAMPLE_ROWS
        assert run(land.fetch_land_possession(PNU)) == result
        assert len(vworld_rows["_session"].calls) == 1

    def test_error_returns_empty(self, vworld_rows, fake_redis: FakeRedis) -> None:
        vworld_rows["getPossessionAttr"] = ConnectionError("down")
        assert run(land.fetch_land_possession(PNU)) == land.POSSESSION_EMPTY
        assert fake_redis.store == {}


#=== 공동주택가격 ===#
def apt_row(year, dong, ho, area, price_won):
    return {"stdrYear": str(year), "aphusNm": "미성", "dongNm": dong, "hoNm": ho, "prvuseAr": area, "pblntfPc": str(price_won)}


class TestApartmentPrices:
    def test_dedupes_units_and_groups_by_area(self, vworld_rows) -> None:
        rows = [
            apt_row(THIS_YEAR, "1", "101", "59.9", 300_000_000),
            apt_row(THIS_YEAR, "1", "101", "59.9", 300_000_000),   # 같은 호 중복 (2024년~ 실측)
            apt_row(THIS_YEAR, "1", "102", "59.9", 500_000_000),
            apt_row(THIS_YEAR, "2", "101", "84.97", 700_000_000),
            apt_row(2001, "9", "999", "84.97", 1),                 # 다른 연도 행은 무시
        ]
        vworld_rows["getApartHousingPriceAttr"] = {"apartHousingPrices": {"field": rows}}

        result = land._request_apartment_prices(PNU)

        assert result == {
            "year": THIS_YEAR,
            "unit_count": 3,
            "official_total": 150_000.0,                           # 만원
            "area_prices": [[59.9, 40_000.0, 2], [84.97, 70_000.0, 1]],
            "fallback": False,
        }

    def test_falls_back_to_last_year(self, vworld_rows) -> None:
        def respond(params):
            if params["stdrYear"] == str(THIS_YEAR):
                return {"apartHousingPrices": {"field": []}}
            return {"apartHousingPrices": {"field": [apt_row(THIS_YEAR - 1, "1", "101", "59.9", 100_000_000)]}}
        vworld_rows["getApartHousingPriceAttr"] = respond

        assert land._request_apartment_prices(PNU)["year"] == THIS_YEAR - 1

    def test_reads_next_page_only_when_page_is_full(self, vworld_rows) -> None:
        full = [apt_row(THIS_YEAR, "1", str(i), "59.9", 100_000_000) for i in range(land.APT_PRICE_PAGE_ROWS)]
        last = [apt_row(THIS_YEAR, "2", "1", "59.9", 100_000_000)]
        vworld_rows["getApartHousingPriceAttr"] = lambda p: {"apartHousingPrices": {"field": full if p["pageNo"] == 1 else last}}

        result = land._request_apartment_prices(PNU)

        assert result["unit_count"] == land.APT_PRICE_PAGE_ROWS + 1
        assert [c["params"]["pageNo"] for c in vworld_rows["_session"].calls] == [1, 2]

    def test_nothing_found_is_fallback(self, vworld_rows) -> None:
        vworld_rows["getApartHousingPriceAttr"] = {"apartHousingPrices": {"field": []}}
        assert land._request_apartment_prices(PNU) == land.APT_PRICE_EMPTY

    def test_fetch_caches_success_only(self, vworld_rows, fake_redis: FakeRedis) -> None:
        vworld_rows["getApartHousingPriceAttr"] = {"apartHousingPrices": {"field": []}}
        run(land.fetch_apartment_prices(PNU))
        assert fake_redis.store == {}

        vworld_rows["getApartHousingPriceAttr"] = {"apartHousingPrices": {"field": [apt_row(THIS_YEAR, "1", "1", "59.9", 1)]}}
        run(land.fetch_apartment_prices(PNU))
        assert len(fake_redis.store) == 1


#=== 개별주택가격 ===#
class TestHousePrice:
    def test_dedupes_and_sums(self, vworld_rows) -> None:
        row = {"stdrYear": str(THIS_YEAR), "dongCode": "1", "buldSn": "1", "buldAllTotAr": "100", "housePc": "250000000"}
        other = {**row, "buldSn": "2", "housePc": "150000000"}
        vworld_rows["getIndvdHousingPriceAttr"] = {"indvdHousingPrices": {"field": [row, row, other]}}

        assert land._request_house_price(PNU) == {"year": THIS_YEAR, "house_price": 40_000.0, "fallback": False}

    def test_error_returns_empty(self, vworld_rows, fake_redis: FakeRedis) -> None:
        vworld_rows["getIndvdHousingPriceAttr"] = ConnectionError("down")
        assert run(land.fetch_house_price(PNU)) == land.HOUSE_PRICE_EMPTY


#=== 건축물대장 (캐시 경유) ===#
class TestBuildingCached:
    def test_caches_success(self, monkeypatch: pytest.MonkeyPatch, fake_redis: FakeRedis) -> None:
        calls = []
        monkeypatch.setattr(land, "fetch_building", lambda pnu: calls.append(pnu) or BuildingLedger(pnu=pnu, floor_area_m2=99.0))

        first = run(land.fetch_building_cached(PNU))
        second = run(land.fetch_building_cached(PNU))

        assert first["floor_area_m2"] == 99.0
        assert second == first
        assert calls == [PNU]

    def test_does_not_cache_fallback(self, monkeypatch: pytest.MonkeyPatch, fake_redis: FakeRedis) -> None:
        monkeypatch.setattr(land, "fetch_building", lambda pnu: BuildingLedger(pnu=pnu, fallback=True))

        assert run(land.fetch_building_cached(PNU))["fallback"] is True
        assert fake_redis.store == {}

    def test_apartment_complex_cached(self, monkeypatch: pytest.MonkeyPatch, fake_redis: FakeRedis) -> None:
        calls = []
        monkeypatch.setattr(land, "fetch_apartment_complex", lambda pnu: calls.append(pnu) or ApartmentComplex(pnu=pnu, households=3930))

        assert run(land.fetch_apartment_complex_cached(PNU))["households"] == 3930
        assert run(land.fetch_apartment_complex_cached(PNU))["households"] == 3930
        assert calls == [PNU]


class TestExclusiveTotalCached:
    def test_fetches_then_caches(self, monkeypatch: pytest.MonkeyPatch, fake_redis: FakeRedis) -> None:
        calls = []
        monkeypatch.setattr(land, "fetch_exclusive_total", lambda pnu: calls.append(pnu) or 1234.5)

        assert run(land.fetch_exclusive_total_cached(PNU)) == 1234.5
        assert run(land.fetch_exclusive_total_cached(PNU)) == 1234.5
        assert calls == [PNU]

    def test_zero_is_not_cached(self, monkeypatch: pytest.MonkeyPatch, fake_redis: FakeRedis) -> None:
        calls = []
        monkeypatch.setattr(land, "fetch_exclusive_total", lambda pnu: calls.append(pnu) or 0.0)

        assert run(land.fetch_exclusive_total_cached(PNU)) == 0.0
        assert run(land.fetch_exclusive_total_cached(PNU)) == 0.0
        assert calls == [PNU, PNU]
