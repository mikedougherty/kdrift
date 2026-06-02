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

    def test_subcommands_listed(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "diff" in result.output
        assert "mcp" in result.output
        assert "lsp" in result.output

    def test_diff_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["diff", "--help"])
        assert result.exit_code == 0
        assert "--format" in result.output
        assert "--check" in result.output
        assert "--watch" in result.output

    def test_lsp_subcommand_exists(self):
        runner = CliRunner()
        result = runner.invoke(main, ["lsp", "--help"])
        assert result.exit_code == 0
        assert "LSP server" in result.output

    def test_mcp_subcommand_exists(self):
        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "--help"])
        assert result.exit_code == 0
        assert "MCP server" in result.output
