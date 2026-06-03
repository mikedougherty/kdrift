"""LSP server for IDE integration.

Provides:
- Diagnostics on save: "this edit changed N resources across M overlays"
- CodeLens: annotations showing which overlays reference each kustomization.yaml
- Hover: show affected overlay count for files in kustomize directories
- Workspace file watching: invalidates graph on file create/delete/rename

Run via: kdrift lsp
Configure in VS Code settings.json, Neovim lspconfig, or any LSP client.
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import importlib
import signal
import sys
from collections.abc import Callable
from pathlib import Path

import lsprotocol.types as lsp
import structlog
from pygls.lsp.server import LanguageServer

from kdrift import config, discover, git, models, pipeline

log: structlog.stdlib.BoundLogger = structlog.get_logger()

_DEBOUNCE_SECONDS = 0.3
_MAX_CODELENS_OVERLAYS = 5

_WATCHED_PATTERNS = ["**/*.yaml", "**/*.yml", "**/*.json"]


@dataclasses.dataclass
class KdriftState:
    """Mutable state for the LSP server."""

    graph: discover.DependencyGraph | None = None
    repo_root: Path | None = None
    graph_stale: bool = True
    last_saved_uri: str | None = None
    pending_rebuild: asyncio.Task[None] | None = None


def _safe_handler[T](fn: Callable[..., T]) -> Callable[..., T | None]:
    """Wrap an LSP handler to catch and log all exceptions without crashing the server."""

    @functools.wraps(fn)
    def wrapper(*args: object, **kwargs: object) -> T | None:
        try:
            return fn(*args, **kwargs)
        except (SystemExit, KeyboardInterrupt):
            raise
        except Exception:
            log.exception("lsp_handler_error", handler=fn.__name__)
            return None

    return wrapper


class KdriftLanguageServer(LanguageServer):
    """LanguageServer subclass with kdrift state and error suppression."""

    def __init__(
        self,
        name: str,
        version: str,
        text_document_sync_kind: lsp.TextDocumentSyncKind = lsp.TextDocumentSyncKind.Incremental,
        notebook_document_sync: lsp.NotebookDocumentSyncOptions | None = None,
    ) -> None:
        """Initialize with kdrift state."""
        super().__init__(name, version, text_document_sync_kind, notebook_document_sync)
        self.state = KdriftState()

    def report_server_error(self, error: Exception, source: object) -> None:
        """Log all server errors to file instead of showing popups."""
        log.exception("lsp_server_error", source=str(source), error=str(error))


server = KdriftLanguageServer(
    name="kdrift-lsp",
    version="0.1.0",
    text_document_sync_kind=lsp.TextDocumentSyncKind.Full,
)


def _get_graph() -> tuple[discover.DependencyGraph, Path] | None:
    """Get or build the dependency graph, caching it on server.state."""
    state = server.state

    if state.graph is not None and state.repo_root is not None and not state.graph_stale:
        return state.graph, state.repo_root

    try:
        repo_root = state.repo_root or git.find_repo_root()
    except git.GitError:
        return None

    try:
        new_graph = discover.DependencyGraph(repo_root)
        new_graph.build()
        state.graph = new_graph
        state.repo_root = repo_root
        state.graph_stale = False
    except Exception:
        log.exception("graph_rebuild_failed")
        if state.graph is not None and state.repo_root is not None:
            log.info("using_cached_graph")
            state.graph_stale = False
            return state.graph, state.repo_root
        return None

    return state.graph, state.repo_root


def _invalidate_graph() -> None:
    """Mark graph stale so next access rebuilds it.

    The old graph is kept as a fallback until the new one builds
    successfully, so a transient parse error in a kustomization.yaml
    doesn't wipe out the cached dependency data.
    """
    server.state.graph_stale = True


def _uri_to_path(uri: str) -> Path:
    """Convert a file:// URI to a Path."""
    if uri.startswith("file://"):
        return Path(uri[7:])
    return Path(uri)


@server.feature(lsp.INITIALIZED)
@_safe_handler
def on_initialized(params: lsp.InitializedParams) -> None:
    """Build graph, prime replay URI, and register file watchers."""
    state = server.state

    result = _get_graph()
    if result is None:
        log.warning("lsp_init_no_graph")
        return

    graph, repo_root = result
    overlay_count = len(graph.leaf_overlays)

    if state.last_saved_uri is None:
        leaves = graph.leaf_overlays
        if leaves:
            first_leaf = leaves[0]
            state.last_saved_uri = (repo_root / first_leaf.kustomization_file).as_uri()
            log.info("default_replay_uri", uri=state.last_saved_uri)

    _register_file_watchers()

    server.window_show_message(
        lsp.ShowMessageParams(
            type=lsp.MessageType.Info,
            message=f"kdrift LSP ready ({overlay_count} leaf overlays)",
        )
    )
    log.info("lsp_ready", overlays=overlay_count)


def _register_file_watchers() -> None:
    """Register workspace file watchers for kustomize-relevant files."""
    caps = server.client_capabilities
    if caps is None:
        return

    workspace = caps.workspace
    if workspace is None:
        return

    dcwf = workspace.did_change_watched_files
    if dcwf is None or not dcwf.dynamic_registration:
        log.info("file_watchers_not_supported")
        return

    watchers = [
        lsp.FileSystemWatcher(
            glob_pattern=pattern,
            kind=lsp.WatchKind.Create | lsp.WatchKind.Delete,
        )
        for pattern in _WATCHED_PATTERNS
    ]

    registration = lsp.Registration(
        id="kdrift-file-watchers",
        method=lsp.WORKSPACE_DID_CHANGE_WATCHED_FILES,
        register_options=lsp.DidChangeWatchedFilesRegistrationOptions(watchers=watchers),
    )

    server.client_register_capability(lsp.RegistrationParams(registrations=[registration]))
    log.info("file_watchers_registered", patterns=_WATCHED_PATTERNS)


@server.feature(lsp.WORKSPACE_DID_CHANGE_WATCHED_FILES)
@_safe_handler
def did_change_watched_files(params: lsp.DidChangeWatchedFilesParams) -> None:
    """Invalidate graph when files are created or deleted."""
    for change in params.changes:
        if change.type in (lsp.FileChangeType.Created, lsp.FileChangeType.Deleted):
            log.debug("file_watch_event", uri=change.uri, type=change.type.name)
            _invalidate_graph()
            return


@server.feature(lsp.TEXT_DOCUMENT_DID_SAVE)
@_safe_handler
def did_save(params: lsp.DidSaveTextDocumentParams) -> None:
    """Run drift detection on save with debouncing."""
    state = server.state
    state.last_saved_uri = params.text_document.uri
    file_path = _uri_to_path(params.text_document.uri)

    if file_path.name in discover.KUSTOMIZATION_FILENAMES:
        _invalidate_graph()

    _schedule_rebuild(params.text_document.uri, file_path)


def _schedule_rebuild(uri: str, file_path: Path) -> None:
    """Schedule a debounced rebuild, cancelling any pending one."""
    state = server.state

    if state.pending_rebuild is not None and not state.pending_rebuild.done():
        state.pending_rebuild.cancel()
        log.debug("rebuild_debounced", uri=uri)

    loop = asyncio.get_event_loop()
    state.pending_rebuild = loop.create_task(_debounced_rebuild(uri, file_path))


async def _debounced_rebuild(uri: str, file_path: Path) -> None:
    """Wait for debounce period, then run diagnostics."""
    try:
        await asyncio.sleep(_DEBOUNCE_SECONDS)
    except asyncio.CancelledError:
        return

    _run_save_diagnostics(uri, file_path)


def _run_save_diagnostics(uri: str, file_path: Path) -> None:
    """Run diagnostics for a saved file."""
    result = _get_graph()
    if result is None:
        return
    graph, repo_root = result

    try:
        rel_path = file_path.relative_to(repo_root)
    except ValueError:
        return

    affected = graph.affected_overlays([rel_path])
    if not affected:
        server.text_document_publish_diagnostics(lsp.PublishDiagnosticsParams(uri=uri, diagnostics=[]))
        return

    diagnostics: list[lsp.Diagnostic] = []

    if git.has_commits(repo_root):
        try:
            proj_config = config.load_project_config(repo_root)
            diff_result = pipeline.run_diff(
                repo_root=repo_root,
                paths=[rel_path],
                kustomize_args=proj_config.kustomize_args,
            )

            total_changes = sum(len(o.changes) for o in diff_result.overlays)
            overlay_names = [str(o.path) for o in diff_result.overlays if o.has_changes]
            error_overlays = [o for o in diff_result.overlays if o.has_error]

            if total_changes > 0:
                diagnostics.append(
                    lsp.Diagnostic(
                        range=lsp.Range(
                            start=lsp.Position(line=0, character=0),
                            end=lsp.Position(line=0, character=0),
                        ),
                        severity=lsp.DiagnosticSeverity.Information,
                        source="kdrift",
                        message=(
                            f"This change affects {total_changes} resource(s) "
                            f"across {len(overlay_names)} overlay(s): "
                            f"{', '.join(overlay_names)}"
                        ),
                    )
                )

            for overlay in error_overlays:
                diagnostics.append(
                    lsp.Diagnostic(
                        range=lsp.Range(
                            start=lsp.Position(line=0, character=0),
                            end=lsp.Position(line=0, character=0),
                        ),
                        severity=lsp.DiagnosticSeverity.Error,
                        source="kdrift",
                        message=f"kustomize build failed for {overlay.path}: {overlay.error}",
                    )
                )

        except Exception:
            log.exception("diff_failed")
            overlay_list = ", ".join(str(o.path) for o in affected)
            diagnostics.append(
                lsp.Diagnostic(
                    range=lsp.Range(
                        start=lsp.Position(line=0, character=0),
                        end=lsp.Position(line=0, character=0),
                    ),
                    severity=lsp.DiagnosticSeverity.Information,
                    source="kdrift",
                    message=f"Affects {len(affected)} overlay(s): {overlay_list}",
                )
            )
    else:
        overlay_list = ", ".join(str(o.path) for o in affected)
        diagnostics.append(
            lsp.Diagnostic(
                range=lsp.Range(
                    start=lsp.Position(line=0, character=0),
                    end=lsp.Position(line=0, character=0),
                ),
                severity=lsp.DiagnosticSeverity.Information,
                source="kdrift",
                message=f"Affects {len(affected)} overlay(s): {overlay_list}",
            )
        )

    server.text_document_publish_diagnostics(lsp.PublishDiagnosticsParams(uri=uri, diagnostics=diagnostics))


@server.feature(lsp.TEXT_DOCUMENT_CODE_LENS)
@_safe_handler
def code_lens(params: lsp.CodeLensParams) -> list[lsp.CodeLens]:
    """Show overlay impact as CodeLens annotations on kustomization.yaml files."""
    file_path = _uri_to_path(params.text_document.uri)

    if file_path.name not in discover.KUSTOMIZATION_FILENAMES:
        return []

    result = _get_graph()
    if result is None:
        return []
    graph, repo_root = result

    try:
        rel_path = file_path.relative_to(repo_root)
    except ValueError:
        return []

    overlay_dir = rel_path.parent
    leaves = graph.leaf_overlays
    is_leaf = any(str(o.path) == str(overlay_dir) for o in leaves)

    lenses: list[lsp.CodeLens] = []

    if is_leaf:
        lenses.append(
            lsp.CodeLens(
                range=lsp.Range(
                    start=lsp.Position(line=0, character=0),
                    end=lsp.Position(line=0, character=0),
                ),
                command=lsp.Command(
                    title=f"kdrift: leaf overlay ({overlay_dir})",
                    command="",
                ),
            )
        )
    else:
        affected = graph.affected_overlays([rel_path])
        if affected:
            overlay_names = ", ".join(str(o.path) for o in affected[:_MAX_CODELENS_OVERLAYS])
            remaining = len(affected) - _MAX_CODELENS_OVERLAYS
            suffix = f" (+{remaining} more)" if remaining > 0 else ""
            lenses.append(
                lsp.CodeLens(
                    range=lsp.Range(
                        start=lsp.Position(line=0, character=0),
                        end=lsp.Position(line=0, character=0),
                    ),
                    command=lsp.Command(
                        title=f"kdrift: renders in {len(affected)} overlay(s): {overlay_names}{suffix}",
                        command="",
                    ),
                )
            )

    return lenses


@server.feature(lsp.TEXT_DOCUMENT_HOVER)
@_safe_handler
def hover(params: lsp.HoverParams) -> lsp.Hover | None:
    """Show affected overlay info on hover."""
    file_path = _uri_to_path(params.text_document.uri)

    result = _get_graph()
    if result is None:
        return None
    graph, repo_root = result

    try:
        rel_path = file_path.relative_to(repo_root)
    except ValueError:
        return None

    affected = graph.affected_overlays([rel_path])
    if not affected:
        return None

    overlay_lines = "\n".join(f"- {o.path}" for o in affected)
    content = f"**kdrift**: this file affects {len(affected)} overlay(s):\n\n{overlay_lines}"

    return lsp.Hover(
        contents=lsp.MarkupContent(
            kind=lsp.MarkupKind.Markdown,
            value=content,
        ),
    )


_ENGINE_MODULES = (
    "kdrift.models",
    "kdrift.config",
    "kdrift.git",
    "kdrift.discover",
    "kdrift.render",
    "kdrift.diff",
    "kdrift.pipeline",
)


def _reload_engine(signum: int, frame: object) -> None:
    """Reload all kdrift engine modules in-place (SIGUSR1 handler)."""
    reloaded = []
    for mod_name in _ENGINE_MODULES:
        if mod_name in sys.modules:
            importlib.reload(sys.modules[mod_name])
            reloaded.append(mod_name)

    server.state.graph_stale = True
    log.info("engine_reloaded", modules=reloaded)
    server.window_show_message(
        lsp.ShowMessageParams(
            type=lsp.MessageType.Info,
            message=f"kdrift engine reloaded ({len(reloaded)} modules)",
        )
    )

    _run_full_diff_diagnostics()


def _run_full_diff_diagnostics() -> None:
    """Run the diff pipeline against all changed files and publish diagnostics."""
    result = _get_graph()
    if result is None:
        log.warning("reload_diff_skipped", reason="no graph")
        return
    graph, repo_root = result

    try:
        changed = git.changed_files("HEAD", repo_root=repo_root)
    except git.GitError:
        log.exception("reload_diff_git_error")
        return

    if not changed:
        log.info("reload_diff_no_changes")
        server.window_show_message(
            lsp.ShowMessageParams(
                type=lsp.MessageType.Info,
                message="kdrift: no uncommitted changes",
            )
        )
        return

    affected = graph.affected_overlays(changed)
    log.info("reload_diff", changed_files=len(changed), affected_overlays=len(affected))

    if not affected:
        server.window_show_message(
            lsp.ShowMessageParams(
                type=lsp.MessageType.Info,
                message=f"kdrift: {len(changed)} changed file(s), no overlays affected",
            )
        )
        return

    try:
        proj_config = config.load_project_config(repo_root)
        diff_result = pipeline.run_diff(
            repo_root=repo_root,
            kustomize_args=proj_config.kustomize_args,
        )
    except Exception:
        log.exception("reload_diff_pipeline_error")
        return

    total_changes = sum(len(o.changes) for o in diff_result.overlays)
    error_count = sum(1 for o in diff_result.overlays if o.has_error)
    msg_parts = [f"{len(affected)} overlay(s)"]
    if total_changes > 0:
        msg_parts.append(f"{total_changes} resource change(s)")
    if error_count > 0:
        msg_parts.append(f"{error_count} build error(s)")
    if total_changes == 0 and error_count == 0:
        msg_parts.append("no drift")

    server.window_show_message(
        lsp.ShowMessageParams(
            type=lsp.MessageType.Info if error_count == 0 else lsp.MessageType.Warning,
            message=f"kdrift: {', '.join(msg_parts)}",
        )
    )

    _publish_overlay_diagnostics(diff_result, repo_root)

    log.info(
        "reload_diff_complete",
        overlays=len(affected),
        changes=total_changes,
        errors=error_count,
    )


def _publish_overlay_diagnostics(diff_result: models.DiffResult, repo_root: Path) -> None:
    """Publish per-overlay diagnostics to VS Code."""
    for overlay_result in diff_result.overlays:
        kust_uri = (repo_root / overlay_result.path / "kustomization.yaml").as_uri()
        diagnostics: list[lsp.Diagnostic] = []

        if overlay_result.has_error:
            diagnostics.append(
                lsp.Diagnostic(
                    range=lsp.Range(
                        start=lsp.Position(line=0, character=0),
                        end=lsp.Position(line=0, character=0),
                    ),
                    severity=lsp.DiagnosticSeverity.Error,
                    source="kdrift",
                    message=f"kustomize build failed: {overlay_result.error}",
                )
            )
        elif overlay_result.has_changes:
            names = [f"{c.resource_id.kind}/{c.resource_id.name}" for c in overlay_result.changes]
            diagnostics.append(
                lsp.Diagnostic(
                    range=lsp.Range(
                        start=lsp.Position(line=0, character=0),
                        end=lsp.Position(line=0, character=0),
                    ),
                    severity=lsp.DiagnosticSeverity.Information,
                    source="kdrift",
                    message=f"{len(overlay_result.changes)} resource(s) changed: {', '.join(names)}",
                )
            )

        server.text_document_publish_diagnostics(lsp.PublishDiagnosticsParams(uri=kust_uri, diagnostics=diagnostics))


def run_lsp_server() -> None:
    """Start the LSP server on stdio."""
    signal.signal(signal.SIGUSR1, _reload_engine)
    log.info("sigusr1_handler_registered")
    server.start_io()
