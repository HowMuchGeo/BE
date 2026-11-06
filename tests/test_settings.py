import pytest

from app.config.settings import MissingSettingError, settings


def test_required_value_is_read_at_access_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VWORLD_API_KEY", "changed")
    assert settings.vworld_api_key == "changed"


def test_missing_required_value_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VWORLD_API_KEY")
    with pytest.raises(MissingSettingError) as exc:
        _ = settings.vworld_api_key
    assert exc.value.key == "VWORLD_API_KEY"


def test_empty_string_counts_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAKAO_API_KEY", "")
    with pytest.raises(MissingSettingError):
        _ = settings.kakao_api_key


@pytest.mark.parametrize(
    ("key", "attr", "raw", "expected"),
    [
        ("PROXY_URL", "proxy_url", "https://proxy.test///", "https://proxy.test"),
        ("BACKEND_URL", "backend_url", "https://api.test/", "https://api.test"),
        ("FRONTEND_URL", "frontend_url", "https://fe.test/", "https://fe.test"),
        ("KAKAO_PAYMENT_BASE_URL", "kakao_payment_base_url", "https://pay.test/v1/", "https://pay.test/v1"),
    ],
)
def test_urls_drop_trailing_slash(monkeypatch: pytest.MonkeyPatch, key: str, attr: str, raw: str, expected: str) -> None:
    monkeypatch.setenv(key, raw)
    assert getattr(settings, attr) == expected


def test_frontend_url_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRONTEND_URL")
    assert settings.frontend_url == ""


def test_optional_keys_return_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MOLIT_API_KEY")
    monkeypatch.delenv("BLDRGST_API_KEY")
    assert settings.molit_api_key is None
    assert settings.bldrgst_api_key is None


def test_bldrgst_key_falls_back_to_legacy_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BLDRGST_API_KEY")
    monkeypatch.setenv("BUILDING_LEDGER_API_KEY", "legacy")
    assert settings.bldrgst_api_key == "legacy"


def test_validate_required_passes_when_all_set() -> None:
    settings.validate_required()


def test_validate_required_lists_every_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VWORLD_API_KEY")
    monkeypatch.delenv("BACKEND_URL")
    with pytest.raises(MissingSettingError) as exc:
        settings.validate_required()
    assert "VWORLD_API_KEY" in exc.value.key
    assert "BACKEND_URL" in exc.value.key
