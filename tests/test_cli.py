"""Tests for the CLI entrypoint."""

import pytest
from click.testing import CliRunner

from kdrift.cli import main


@pytest.mark.unit
class TestCLI:
    """Tests for the CLI commands."""

    def test_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Kustomize manifest drift detection tool" in result.output

    def test_run_command(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["run"])
        assert result.exit_code == 0
        assert "Hello from kdrift" in result.output
