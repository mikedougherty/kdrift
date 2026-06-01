"""Tests for logging configuration."""

import logging

import pytest

from kdrift import logging as app_logging


@pytest.mark.unit
class TestConfigureLogging:
    """Tests for configure_logging."""

    def test_json_format(self) -> None:
        app_logging.configure_logging(log_level="INFO", log_format="json")
        root = logging.getLogger()
        assert root.level == logging.INFO
        assert len(root.handlers) == 1

    def test_console_format(self) -> None:
        app_logging.configure_logging(log_level="DEBUG", log_format="console")
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_replaces_existing_handlers(self) -> None:
        logging.getLogger().addHandler(logging.StreamHandler())
        app_logging.configure_logging()
        assert len(logging.getLogger().handlers) == 1
