import pytest

from app.clients.http import RETRY_STATUS, ExternalAPIError, build_session, parse_json
from tests.fakes import FakeResponse


class TestParseJson:
    def test_returns_dict(self) -> None:
        assert parse_json(FakeResponse({"a": 1}), "api") == {"a": 1}

    def test_non_2xx_raises_with_status(self) -> None:
        with pytest.raises(ExternalAPIError) as exc:
            parse_json(FakeResponse({"a": 1}, status_code=502), "getX")
        assert exc.value.api == "getX"
        assert exc.value.status_code == 502

    def test_non_json_body_raises_with_preview(self) -> None:
        with pytest.raises(ExternalAPIError) as exc:
            parse_json(FakeResponse(text="<html>  proxy   error </html>", content_type="text/html"), "getX")
        assert "non-JSON" in str(exc.value)
        assert "<html> proxy error </html>" in str(exc.value)

    def test_non_dict_json_raises(self) -> None:
        with pytest.raises(ExternalAPIError, match="unexpected top-level type"):
            parse_json(FakeResponse([1, 2]), "getX")


def test_build_session_mounts_retry_for_https() -> None:
    session = build_session(total=4, backoff_factor=0.3, raise_on_status=False)
    retry = session.get_adapter("https://example.test").max_retries

    assert retry.total == 4
    assert retry.backoff_factor == 0.3
    assert tuple(retry.status_forcelist) == RETRY_STATUS
    assert retry.raise_on_status is False
    assert "GET" in retry.allowed_methods
    assert "POST" not in retry.allowed_methods
