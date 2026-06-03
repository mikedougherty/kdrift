"""LSP server for IDE integration.

Provides:
- Diagnostics on save: "this edit changed N resources across M overlays"
- CodeLens: annotations showing which overlays reference each kustomization.yaml
- Hover: show affected overlay count for files in kustomize directories

Run via: kdrift lsp
Configure in VS Code settings.json, Neovim lspconfig, or any LSP client.
"""

from __future__ import annotations

import functools
import importlib
import signal
import sys
from collections.abc import Callable
from pathlib import Path

import lsprotocol.types as lsp
import structlog
from pygls.lsp.server import LanguageServer

from kdrift import config, discover, git, pipeline

log: structlog.stdlib.BoundLogger = structlog.get_logger()


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
    """LanguageServer subclass that logs errors instead of crashing."""

    def report_server_error(self, error: Exception, source: object) -> None:
        """Log all server errors to file instead of showing popups."""
        log.exception("lsp_server_error", source=str(source), error=str(error))


server = KdriftLanguageServer(
    name="kdrift-lsp",
    version="0.1.0",
    text_document_sync_kind=lsp.TextDocumentSyncKind.Full,
)

_graph: discover.DependencyGraph | None = None
_repo_root: Path | None = None
_graph_stale: bool = True
_last_saved_uri: str | None = None

_MAX_CODELENS_OVERLAYS = 5


def _get_graph() -> tuple[discover.DependencyGraph, Path] | None:
    """Get or build the dependency graph, caching it."""
    global _graph, _repo_root, _graph_stale  # noqa: PLW0603

    if _graph is not None and _repo_root is not None and not _graph_stale:
        return _graph, _repo_root

    try:
        repo_root = _repo_root or git.find_repo_root()
    except git.GitError:
        return None

    try:
        new_graph = discover.DependencyGraph(repo_root)
        new_graph.build()
        _graph = new_graph
        _repo_root = repo_root
        _graph_stale = False
    except Exception:
        log.exception("graph_rebuild_failed")
        if _graph is not None and _repo_root is not None:
            log.info("using_cached_graph")
            _graph_stale = False
            return _graph, _repo_root
        return None

    return _graph, _repo_root


def _invalidate_graph() -> None:
    """Mark graph stale so next access rebuilds it.

    The old graph is kept as a fallback until the new one builds
    successfully, so a transient parse error in a kustomization.yaml
    doesn't wipe out the cached dependency data.
    """
    global _graph_stale  # noqa: PLW0603
    _graph_stale = True


def _uri_to_path(uri: str) -> Path:
    """Convert a file:// URI to a Path."""
    if uri.startswith("file://"):
        return Path(uri[7:])
    return Path(uri)


@server.feature(lsp.INITIALIZED)
@_safe_handler
def on_initialized(params: lsp.InitializedParams) -> None:
    """Notify the user when the server is ready."""
    result = _get_graph()
    overlay_count = len(result[0].leaf_overlays) if result else 0
    server.window_show_message(
        lsp.ShowMessageParams(
            type=lsp.MessageType.Info,
            message=f"kdrift LSP ready ({overlay_count} leaf overlays)",
        )
    )
    log.info("lsp_ready", overlays=overlay_count)


@server.feature(lsp.TEXT_DOCUMENT_DID_SAVE)
@_safe_handler
def did_save(params: lsp.DidSaveTextDocumentParams) -> None:
    """Run drift detection on save and publish diagnostics."""
    global _last_saved_uri  # noqa: PLW0603
    _last_saved_uri = params.text_document.uri
    file_path = _uri_to_path(params.text_document.uri)

    if file_path.name in discover.KUSTOMIZATION_FILENAMES:
        _invalidate_graph()

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
        server.text_document_publish_diagnostics(
            lsp.PublishDiagnosticsParams(
                uri=params.text_document.uri,
                diagnostics=[],
            )
        )
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

    server.text_document_publish_diagnostics(
        lsp.PublishDiagnosticsParams(
            uri=params.text_document.uri,
            diagnostics=diagnostics,
        )
    )


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
    global _graph_stale  # noqa: PLW0603
    reloaded = []
    for mod_name in _ENGINE_MODULES:
        if mod_name in sys.modules:
            importlib.reload(sys.modules[mod_name])
            reloaded.append(mod_name)

    _graph_stale = True
    log.info("engine_reloaded", modules=reloaded)
    server.window_show_message(
        lsp.ShowMessageParams(
            type=lsp.MessageType.Info,
            message=f"kdrift engine reloaded ({len(reloaded)} modules)",
        )
    )

    if _last_saved_uri is not None:
        log.info("replay_last_save", uri=_last_saved_uri)
        did_save(
            lsp.DidSaveTextDocumentParams(
                text_document=lsp.TextDocumentIdentifier(uri=_last_saved_uri),
            )
        )


def run_lsp_server() -> None:
    """Start the LSP server on stdio."""
    signal.signal(signal.SIGUSR1, _reload_engine)
    log.info("sigusr1_handler_registered")
    server.start_io()
