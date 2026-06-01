"""Shared test fixtures."""

import pytest

from kdrift import config


@pytest.fixture()
def app_config() -> config.AppConfig:
    """Provide a test AppConfig with defaults."""
    return config.AppConfig()
