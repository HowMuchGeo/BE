from datetime import date

import pytest

import app.clients.building_hub as building_hub
import app.services.building_ledger_service as ledger

PNU = "1135010200100170000"          # 노원구 월계동 17
SAN_PNU = "1135010200200130001"      # 산 13-1


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch):
    """building_hub 의 엔드포인트 함수를 교체. hub["titles"] = [...] 처럼 행을 등록한다"""
    data = {"titles": [], "expos": [], "expos_total": None, "recap": [], "atch": [], "error": None, "calls": []}

    def make(name):
        def fake(params, max_pages=1):
            data["calls"].append((name, dict(params), max_pages))
            if data["error"]:
                raise data["error"]
            rows = data[name]
            total = data.get(f"{name}_total")
            return rows, (len(rows) if total is None else total)
        return fake

    monkeypatch.setattr(building_hub, "get_titles", make("titles"))
    monkeypatch.setattr(building_hub, "get_expos_pubuse_areas", make("expos"))
    monkeypatch.setattr(building_hub, "get_recap_titles", make("recap"))
    monkeypatch.setattr(building_hub, "get_atch_jibuns", make("atch"))
    return data


#=== PNU · 경과연수 ===#
@pytest.mark.parametrize(
    ("pnu", "expected"),
    [
        (PNU, ("11350", "10200", "0", "0017", "0000")),
        (SAN_PNU, ("11350", "10200", "1", "0013", "0001")),
        (" " + PNU + " ", ("11350", "10200", "0", "0017", "0000")),
        ("123", None),
        ("11350102001001700AB", None),
        ("", None),
    ],
)
def test_parse_pnu(pnu: str, expected) -> None:
    assert ledger.parse_pnu(pnu) == expected


@pytest.mark.parametrize(
    ("approval", "expected"),
    [
        ("20160101", 10.0),
        ("201607", 9.5),
        ("2016", 10.0),
        ("", 0.0),
        ("abcd", 0.0),
        ("20300101", 0.0),     # 미래 날짜는 0
    ],
)
def test_elapsed_years(approval: str, expected: float) -> None:
    assert ledger.elapsed_years(approval, as_of=date(2026, 1, 15)) == pytest.approx(expected)


#=== 표제부 요약 ===#
class TestFetchBuilding:
    def test_bad_pnu_is_fallback(self, hub) -> None:
        result = ledger.fetch_building("bad")
        assert result.fallback is True
        assert hub["calls"] == []

    def test_missing_key_is_fallback(self, hub, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BLDRGST_API_KEY")
        result = ledger.fetch_building(PNU)
        assert result.fallback is True
        assert "BLDRGST_API_KEY" in result.warnings[0]
        assert hub["calls"] == []

    def test_api_error_is_fallback(self, hub) -> None:
        hub["error"] = RuntimeError("503")
        result = ledger.fetch_building(PNU)
        assert result.fallback is True
        assert result.has_building is False

    def test_no_titles_means_vacant_land(self, hub) -> None:
        result = ledger.fetch_building(PNU)
        assert result.fallback is False
        assert result.has_building is False
        assert result.floor_area_m2 == 0.0

    def test_sends_pnu_params(self, hub) -> None:
        ledger.fetch_building(SAN_PNU)
        name, params, max_pages = hub["calls"][0]
        assert name == "titles"
        assert params == {"sigunguCd": "11350", "bjdongCd": "10200", "platGbCd": "1", "bun": "0013", "ji": "0001"}
        assert max_pages == ledger.MAX_TITLE_PAGES

    def test_summarizes_titles(self, hub) -> None:
        hub["titles"] = [
            #연면적이 가장 큰 동은 지하주차장이지만 세대가 없다 → 대표 동에서 빠진다
            {"totArea": "9000", "hhldCnt": "0", "fmlyCnt": "0", "mainPurpsCdNm": "공동주택", "etcPurps": "지하주차장",
             "grndFlrCnt": "0", "strctCdNm": "철근콘크리트구조", "useAprDay": "19860101", "regstrKindCdNm": "집합"},
            {"totArea": "5000", "hhldCnt": "120", "fmlyCnt": "0", "mainPurpsCdNm": "공동주택", "etcPurps": "아파트",
             "grndFlrCnt": "15", "strctCdNm": "철근콘크리트구조", "useAprDay": "19860601", "regstrKindCdNm": "집합"},
            {"totArea": "1000", "hhldCnt": "30", "fmlyCnt": "2", "mainPurpsCdNm": "공동주택", "etcPurps": "아파트",
             "grndFlrCnt": "5", "strctCdNm": "벽돌구조", "useAprDay": "19900101", "regstrKindCdNm": "집합"},
        ]

        result = ledger.fetch_building(PNU, as_of=date(2026, 6, 1))

        assert result.has_building is True
        assert result.floor_area_m2 == 15000.0
        assert result.etc_purpose == "아파트"
        assert result.floors == 15
        assert result.approval_ymd == "19860601"
        assert result.elapsed_years == pytest.approx(40.0)
        assert result.household_count == 150
        assert result.is_condo is True

    def test_detached_house_counts_family(self, hub) -> None:
        #단독주택은 hhldCnt=0, fmlyCnt=1 / 다가구는 fmlyCnt 가 가구수
        hub["titles"] = [{"totArea": "200", "hhldCnt": "0", "fmlyCnt": "4", "mainPurpsCdNm": "단독주택", "regstrKindCdNm": "일반"}]

        result = ledger.fetch_building(PNU)

        assert result.household_count == 4
        assert result.is_condo is True

    def test_single_household_minimum_is_one(self, hub) -> None:
        hub["titles"] = [{"totArea": "200", "hhldCnt": "", "fmlyCnt": "", "mainPurpsCdNm": "제2종근린생활시설", "regstrKindCdNm": "일반"}]

        result = ledger.fetch_building(PNU)

        assert result.household_count == 1
        assert result.is_condo is False


#=== 전유면적 합계 ===#
class TestExclusiveTotal:
    def test_sums_exclusive_main_building_only(self, hub) -> None:
        hub["expos"] = [
            {"area": "59.9", "exposPubuseGbCdNm": "전유", "mainAtchGbCdNm": "주건축물"},
            {"area": "84.9", "exposPubuseGbCdNm": "전유", "mainAtchGbCdNm": "주건축물"},
            {"area": "20", "exposPubuseGbCdNm": "공용", "mainAtchGbCdNm": "주건축물"},
            {"area": "10", "exposPubuseGbCdNm": "전유", "mainAtchGbCdNm": "부속건축물"},
        ]
        assert ledger.fetch_exclusive_total(PNU) == pytest.approx(144.8)
        assert hub["calls"][0][2] == ledger.MAX_EXPOS_PAGES

    def test_partial_rows_give_zero(self, hub) -> None:
        #다 못 받았으면 합계가 과소집계된다 → 0 (세대수 균등분할로 떨어진다)
        hub["expos"] = [{"area": "59.9", "exposPubuseGbCdNm": "전유", "mainAtchGbCdNm": "주건축물"}]
        hub["expos_total"] = 3670
        assert ledger.fetch_exclusive_total(PNU) == 0.0

    @pytest.mark.parametrize("pnu", ["bad", PNU])
    def test_bad_pnu_or_missing_key_gives_zero(self, hub, monkeypatch: pytest.MonkeyPatch, pnu: str) -> None:
        if pnu == PNU:
            monkeypatch.delenv("BLDRGST_API_KEY")
        assert ledger.fetch_exclusive_total(pnu) == 0.0
        assert hub["calls"] == []

    def test_api_error_gives_zero(self, hub) -> None:
        hub["error"] = RuntimeError("503")
        assert ledger.fetch_exclusive_total(PNU) == 0.0


#=== 아파트 단지 ===#
class TestApartmentComplex:
    def test_uses_recap_and_builds_annex_pnus(self, hub) -> None:
        hub["recap"] = [
            {"hhldCnt": "3930", "vlRatEstmTotArea": "300000"},
            {"hhldCnt": "0", "vlRatEstmTotArea": "300000"},
        ]
        hub["titles"] = [
            {"totArea": "280000", "mainPurpsCdNm": "공동주택"},
            {"totArea": "5000", "mainPurpsCdNm": "제2종근린생활시설", "hoCnt": "0", "hhldCnt": "55",
             "strctCdNm": "철근콘크리트구조", "useAprDay": "19860101"},
        ]
        hub["atch"] = [
            {"atchBun": "0017", "atchJi": "0000", "atchPlatGbCd": "0"},
            {"atchBun": "0017", "atchJi": "0000", "atchPlatGbCd": "0"},     # 동마다 반복 → 한 번만
            {"atchBun": "0013", "atchJi": "", "atchPlatGbCd": "1"},          # 산
            {"atchBun": "", "atchJi": "1"},                                  # 본번 없음 → 무시
        ]

        result = ledger.fetch_apartment_complex("1135010200100130000")

        assert result.fallback is False
        assert result.recap_found is True
        assert result.households == 3930
        assert result.far_area_m2 == 300000.0
        assert result.total_floor_area_m2 == 285000.0
        assert result.commercial_units == 55
        assert result.commercial_floor_area_m2 == 5000.0
        assert result.commercial_structure == "철근콘크리트구조"
        assert result.annex_pnus == ["1135010200100170000", "1135010200200130000"]
        assert [c[0] for c in hub["calls"]] == ["recap", "titles", "atch"]

    def test_annex_equal_to_self_is_excluded(self, hub) -> None:
        hub["atch"] = [{"atchBun": "0017", "atchJi": "0000", "atchPlatGbCd": "0"}]
        assert ledger.fetch_apartment_complex(PNU).annex_pnus == []

    def test_without_recap_uses_titles(self, hub) -> None:
        hub["titles"] = [
            {"totArea": "1000", "hhldCnt": "20", "mainPurpsCdNm": "공동주택", "vlRatEstmTotArea": "900"},
            {"totArea": "100", "hhldCnt": "3", "mainPurpsCdNm": "제1종근린생활시설", "vlRatEstmTotArea": "100"},
        ]

        result = ledger.fetch_apartment_complex(PNU)

        assert result.recap_found is False
        assert result.households == 20
        assert result.far_area_m2 == 1000.0

    def test_error_is_fallback(self, hub) -> None:
        hub["error"] = RuntimeError("503")
        assert ledger.fetch_apartment_complex(PNU).fallback is True

    def test_missing_key_is_fallback(self, hub, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BLDRGST_API_KEY")
        assert ledger.fetch_apartment_complex(PNU).fallback is True
        assert hub["calls"] == []
