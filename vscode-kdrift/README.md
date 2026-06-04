# kdrift for VS Code

Kustomize manifest drift detection in your editor. Shows what your changes will do to rendered Kubernetes manifests before you commit, push, or apply.

Modeled after VS Code's built-in Markdown Preview: edit a kustomization file on the left, see the rendered manifest diff on the right.

<!-- TODO: Add screenshots
![Hero: side-by-side editor and drift preview](media/screenshots/hero.png)
-->

## Features

**Live drift preview** — Save a file, see which Kubernetes resources changed across every affected overlay. Color-coded unified diffs with MODIFIED/ADDED/REMOVED badges.

<!-- TODO: ![Multi-overlay blast radius](media/screenshots/multi-overlay.png) -->

**Blast radius detection** — Edit a base file and instantly see the impact across dev, staging, and prod overlays. No more guessing which environments are affected.

**Generator-aware matching** — ConfigMap and Secret names with kustomize hash suffixes are matched intelligently. Renaming a generator shows as ADDED + REMOVED, not a wall of unrelated diffs.

<!-- TODO: ![Generator matching](media/screenshots/generator-matching.png) -->

**Error reporting** — When `kustomize build` fails for an overlay, the error appears inline. Other overlays still render normally.

<!-- TODO: ![Error state](media/screenshots/error-state.png) -->

**LSP integration** — Diagnostics in the Problems panel, CodeLens annotations on `kustomization.yaml` files, and hover info showing affected overlay counts. Powered by `kdrift lsp`.

## Commands

| Command | Description |
|---------|-------------|
| `kdrift: Open Drift Preview to the Side` | Preview follows the active editor |
| `kdrift: Open Locked Drift Preview to the Side` | Preview stays pinned to the current file |
| `kdrift: Refresh Preview` | Manually refresh the preview |

## Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `kdrift.binaryPath` | `"kdrift"` | Path to the kdrift binary |
| `kdrift.debounceInterval` | `500` | Milliseconds before preview refresh after save |
| `kdrift.ref` | `"HEAD"` | Baseline git ref for comparison |
| `kdrift.kustomizeArgs` | `[]` | Extra kustomize build args (overrides `.kdrift.yaml`) |
| `kdrift.lsp.enabled` | `true` | Enable LSP server for diagnostics and CodeLens |
| `kdrift.lsp.debug` | `false` | Enable LSP debug logging |

## Requirements

- **kdrift** installed and on PATH: `uv tool install kdrift`
- **kustomize** installed and on PATH
- A git repository with kustomization.yaml files

## Architecture

The extension uses two independent communication channels with kdrift:

```
                          ┌─────────────────────────┐
                          │   VS Code Extension      │
                          │                          │
  Editor ──── save ──────►│  diffRunner.ts           │
                          │    └─ kdrift diff --json  │──► Webview Preview
                          │                          │     (colored diffs)
                          │  lspClient.ts            │
                          │    └─ kdrift lsp (stdio)  │──► Problems Panel
                          │                          │     CodeLens, Hover
                          └─────────────────────────┘
```

- **CLI** (`kdrift diff --format json`): provides structured diff data for the webview. Scoped to the saved file's path for fast, targeted results.
- **LSP** (`kdrift lsp`): provides diagnostics, CodeLens, and hover via the standard Language Server Protocol.

No Python-side changes were needed. Both interfaces were already stable.

## Development

```bash
cd vscode-kdrift
npm install
npm run build      # build extension + webview
npm run watch      # rebuild on change
npm run lint       # type-check
```

**Testing locally:** Open the `vscode-kdrift/` directory in VS Code and press F5 to launch an Extension Development Host. Open any repository with kustomization.yaml files and use the command palette to open the drift preview.

## Project Structure

```
src/
  extension.ts              # Activation, commands, lifecycle
  types.ts                  # TypeScript interfaces (mirrors kdrift's Pydantic models)
  diffRunner.ts             # Shells out to kdrift CLI, parses JSON
  driftPreviewProvider.ts   # Webview panel management (locked/unlocked modes)
  lspClient.ts              # vscode-languageclient setup
  webview/
    preview.ts              # In-webview rendering (diff tables, badges, states)
    styles.css              # Theme-aware CSS using VS Code variables
```
