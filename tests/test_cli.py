"""Tests for the CLI entrypoint."""

import pytest
from click.testing import CliRunner

from kdrift.cli import main


@pytest.mark.unit
class TestCLI:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Kustomize manifest drift detection tool" in result.output

    def test_no_repo_error(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(main, [], catch_exceptions=False)
        assert result.exit_code in (0, 1)

    def test_json_format_option(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "--format" in result.output
        assert "unified" in result.output
        assert "json" in result.output
