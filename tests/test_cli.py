"""Tests for the CLI entrypoint."""

import click
import pytest
from click.testing import CliRunner

from kdrift.cli import _parse_ref_range, main


@pytest.mark.unit
class TestParseRefRange:
    def test_single_ref(self):
        base, target = _parse_ref_range("HEAD")
        assert base == "HEAD"
        assert target is None

    def test_ref_range(self):
        base, target = _parse_ref_range("main~5..main~2")
        assert base == "main~5"
        assert target == "main~2"

    def test_sha_range(self):
        base, target = _parse_ref_range("abc1234..def5678")
        assert base == "abc1234"
        assert target == "def5678"

    def test_empty_left_side(self):
        with pytest.raises(click.BadParameter, match="both sides"):
            _parse_ref_range("..HEAD")

    def test_empty_right_side(self):
        with pytest.raises(click.BadParameter, match="both sides"):
            _parse_ref_range("HEAD..")

    def test_branch_names_with_slashes(self):
        base, target = _parse_ref_range("origin/main..feature/foo")
        assert base == "origin/main"
        assert target == "feature/foo"


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

    def test_diff_ref_range_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["diff", "--help"])
        assert "A..B" in result.output

    def test_diff_watch_with_ref_range_errors(self):
        runner = CliRunner()
        result = runner.invoke(main, ["diff", "--ref", "HEAD~1..HEAD", "--watch"])
        assert result.exit_code != 0
        assert "not supported with ref ranges" in result.output
