"""Tests for application configuration."""

import pydantic
import pytest

from kdrift import config


@pytest.mark.unit
class TestAppConfig:
    """Tests for AppConfig."""

    def test_defaults(self) -> None:
        cfg = config.AppConfig()
        assert cfg.log_level == "INFO"
        assert cfg.log_format == "json"

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_LOG_LEVEL", "DEBUG")
        cfg = config.AppConfig()
        assert cfg.log_level == "DEBUG"

    def test_frozen(self) -> None:
        cfg = config.AppConfig()
        with pytest.raises(pydantic.ValidationError):
            cfg.log_level = "DEBUG"  # type: ignore[misc]
