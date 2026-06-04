"""CLI entrypoint."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
import structlog

from kdrift import config, git, models, pipeline
from kdrift import logging as kdrift_logging
from kdrift import watch as kdrift_watch

log: structlog.stdlib.BoundLogger = structlog.get_logger()


@click.group()
@click.option("--log-level", default="WARNING", help="Log level (DEBUG, INFO, WARNING, ERROR).")
@click.pass_context
def main(ctx: click.Context, log_level: str) -> None:
    """Kustomize manifest drift detection tool."""
    cfg = config.AppConfig()
    kdrift_logging.configure_logging(log_level=log_level, log_format=cfg.log_format, stream="stderr")

    ctx.ensure_object(dict)
    ctx.obj["config"] = cfg
    ctx.obj["log_level"] = log_level


def _parse_ref_range(ref: str) -> tuple[str, str | None]:
    """Parse a ref or ref range (A..B) into (base_ref, target_ref)."""
    if ".." in ref:
        parts = ref.split("..", 1)
        if not parts[0] or not parts[1]:
            msg = f"Invalid ref range '{ref}': both sides of '..' are required"
            raise click.BadParameter(msg, param_hint="'--ref'")
        return parts[0], parts[1]
    return ref, None


@main.command()
@click.argument("paths", nargs=-1, type=click.Path(exists=False))
@click.option("--repo", "-C", "repo_path", type=click.Path(exists=True), default=None, help="Repository root.")
@click.option("--ref", default="HEAD", help="Git ref for baseline, or A..B for two-ref comparison.")
@click.option("--overlay", type=click.Path(), default=None, help="Diff only this overlay.")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["unified", "json"]),
    default="unified",
    help="Output format.",
)
@click.option("--watch", "watch_mode", is_flag=True, help="Watch for changes and re-diff continuously.")
@click.option("--check", is_flag=True, help="Exit non-zero if any overlay has drift (CI/pre-commit mode).")
@click.pass_context
def diff(
    ctx: click.Context,
    paths: tuple[str, ...],
    repo_path: str | None,
    ref: str,
    overlay: str | None,
    output_format: str,
    watch_mode: bool,
    check: bool,
) -> None:
    """Diff kustomize overlays against a baseline ref."""
    start = Path(repo_path) if repo_path else (Path(paths[0]).resolve() if paths else None)
    try:
        repo_root = git.find_repo_root(start)
    except git.GitError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not git.has_commits(repo_root):
        click.echo("Error: repository has no commits yet", err=True)
        sys.exit(1)

    base_ref, target_ref = _parse_ref_range(ref)

    proj_config = config.load_project_config(repo_root)
    path_list = [Path(p) for p in paths] if paths else None

    if watch_mode:
        if target_ref is not None:
            click.echo("Error: --watch is not supported with ref ranges (A..B)", err=True)
            sys.exit(1)
        kdrift_watch.watch(
            repo_root=repo_root,
            ref=base_ref,
            paths=path_list,
            output_format=output_format,
            kustomize_args=proj_config.kustomize_args,
        )
        return

    overlay_path = Path(overlay) if overlay else None

    try:
        result = pipeline.run_diff(
            repo_root=repo_root,
            ref=base_ref,
            paths=path_list,
            overlay_filter=overlay_path,
            kustomize_args=proj_config.kustomize_args,
            target_ref=target_ref,
        )
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if output_format == "json":
        _print_json(result)
    else:
        _print_unified(result)

    if result.has_errors:
        sys.exit(1)
    if check and result.has_changes:
        sys.exit(1)
    if not result.has_changes:
        sys.exit(0)


@main.command()
@click.option("--debug", is_flag=True, help="Enable debug logging and file log (~/.cache/kdrift/kdrift.log).")
@click.pass_context
def mcp(ctx: click.Context, debug: bool) -> None:
    """Start the MCP server for AI agent integration."""
    log_level = "DEBUG" if debug else ctx.obj["log_level"]
    kdrift_logging.configure_logging(log_level=log_level, stream="stderr", log_file=debug)
    from kdrift import mcp_server

    mcp_server.run_mcp_server()


@main.command()
@click.option("--debug", is_flag=True, help="Enable debug logging and file log (~/.cache/kdrift/kdrift.log).")
@click.pass_context
def lsp(ctx: click.Context, debug: bool) -> None:
    """Start the LSP server for IDE integration."""
    log_level = "DEBUG" if debug else ctx.obj["log_level"]
    kdrift_logging.configure_logging(log_level=log_level, stream="stderr", log_file=debug)
    from kdrift import lsp_server

    lsp_server.run_lsp_server()


def _print_json(result: models.DiffResult) -> None:
    """Print structured JSON output."""
    output = json.loads(result.model_dump_json())
    click.echo(json.dumps(output, indent=2))


def _print_unified(result: models.DiffResult) -> None:
    """Print unified diff output."""
    if not result.has_changes and not result.has_errors:
        return

    for overlay_result in result.overlays:
        if overlay_result.has_error:
            click.echo(f"ERROR [{overlay_result.path}]: {overlay_result.error}", err=True)
            continue

        if not overlay_result.has_changes:
            continue

        click.echo(f"=== {overlay_result.path} ===")
        for change in overlay_result.changes:
            status_marker = {
                models.DiffStatus.ADDED: "[NEW]",
                models.DiffStatus.REMOVED: "[DEL]",
                models.DiffStatus.MODIFIED: "",
            }[change.status]

            rid = change.resource_id
            header = f"{rid.gvk} {rid.namespace}/{rid.name}" if rid.namespace else f"{rid.gvk} {rid.name}"
            if status_marker:
                header = f"{status_marker} {header}"

            click.echo(f"\n--- {header} ---")
            if change.diff_text:
                click.echo(change.diff_text)
        click.echo()

    for error in result.errors:
        click.echo(f"ERROR: {error}", err=True)
