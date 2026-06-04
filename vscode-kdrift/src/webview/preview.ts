declare function acquireVsCodeApi(): {
  postMessage(msg: unknown): void;
  getState(): unknown;
  setState(state: unknown): void;
};

interface ResourceId {
  group: string;
  version: string;
  kind: string;
  namespace: string;
  name: string;
}

interface ResourceChange {
  resource_id: ResourceId;
  status: "modified" | "added" | "removed";
  diff_text: string;
  lines_added: number;
  lines_removed: number;
}

interface OverlayResult {
  path: string;
  changes: ResourceChange[];
  error: string | null;
}

interface DiffResult {
  ref: string;
  target_ref: string | null;
  overlays: OverlayResult[];
  errors: string[];
}

type ToWebviewMessage =
  | { type: "update"; data: DiffResult; filePath: string }
  | { type: "loading"; filePath: string }
  | { type: "error"; message: string; filePath: string }
  | { type: "noChanges"; filePath: string };

const vscode = acquireVsCodeApi();
const root = document.getElementById("root")!;

window.addEventListener("message", (event: MessageEvent<ToWebviewMessage>) => {
  const msg = event.data;
  switch (msg.type) {
    case "loading":
      root.innerHTML = renderLoading(msg.filePath);
      break;
    case "update":
      root.innerHTML = renderDiffResult(msg.data, msg.filePath);
      break;
    case "error":
      root.innerHTML = renderError(msg.message, msg.filePath);
      break;
    case "noChanges":
      root.innerHTML = renderNoChanges(msg.filePath);
      break;
  }
});

document.addEventListener("click", (e) => {
  const target = e.target as HTMLElement;
  if (target.classList.contains("refresh-btn")) {
    vscode.postMessage({ type: "refresh" });
  }
});

vscode.postMessage({ type: "ready" });

function renderLoading(filePath: string): string {
  return `
    ${statusBar(filePath)}
    <div class="loading">
      <div class="spinner"></div>
      <p>Running kdrift diff...</p>
    </div>`;
}

function renderNoChanges(filePath: string): string {
  return `
    ${statusBar(filePath)}
    <div class="empty-state">
      <p>No drift detected. Rendered manifests match the baseline.</p>
    </div>`;
}

function renderError(message: string, filePath: string): string {
  return `
    ${statusBar(filePath)}
    <div class="error-state">
      <span class="error-icon">&#x26A0;</span>
      <pre>${escapeHtml(message)}</pre>
    </div>`;
}

function renderDiffResult(result: DiffResult, filePath: string): string {
  let totalAdded = 0;
  let totalRemoved = 0;
  let totalChanges = 0;
  let errorCount = 0;
  for (const o of result.overlays) {
    totalChanges += o.changes.length;
    if (o.error) errorCount++;
    for (const c of o.changes) {
      totalAdded += c.lines_added;
      totalRemoved += c.lines_removed;
    }
  }

  const refBadges = `<span class="ref-badge">ref: ${escapeHtml(result.ref)}</span>
      ${result.target_ref ? `<span class="ref-badge">target: ${escapeHtml(result.target_ref)}</span>` : ""}`;

  let html = `
    ${statusBar(filePath, refBadges)}
    <div class="summary">
      <span>${result.overlays.length} overlay${result.overlays.length !== 1 ? "s" : ""}</span>
      <span>${totalChanges} resource${totalChanges !== 1 ? "s" : ""} changed</span>
      <span class="additions">+${totalAdded}</span>
      <span class="deletions">-${totalRemoved}</span>
      ${errorCount > 0 ? `<span class="error-count">${errorCount} error${errorCount !== 1 ? "s" : ""}</span>` : ""}
    </div>`;

  if (result.errors.length > 0) {
    html += `<div class="global-errors">`;
    for (const err of result.errors) {
      html += `<div class="error-state"><pre>${escapeHtml(err)}</pre></div>`;
    }
    html += `</div>`;
  }

  for (const overlay of result.overlays) {
    html += renderOverlay(overlay);
  }

  return html;
}

function renderOverlay(overlay: OverlayResult): string {
  const changeCount = overlay.changes.length;
  const added = overlay.changes.reduce((s, c) => s + c.lines_added, 0);
  const removed = overlay.changes.reduce((s, c) => s + c.lines_removed, 0);

  let statsHtml = "";
  if (changeCount > 0) {
    statsHtml = `<span class="overlay-stats">${changeCount} changed <span class="additions">+${added}</span> <span class="deletions">-${removed}</span></span>`;
  }

  let html = `
    <details class="overlay" open>
      <summary class="overlay-header">
        <span class="overlay-path">${escapeHtml(overlay.path)}</span>
        ${statsHtml}
        ${overlay.error ? '<span class="badge badge-error">ERROR</span>' : ""}
      </summary>
      <div class="overlay-body">`;

  if (overlay.error) {
    html += `<div class="error-state"><pre>${escapeHtml(overlay.error)}</pre></div>`;
  }

  for (const change of overlay.changes) {
    html += renderChange(change);
  }

  html += `</div></details>`;
  return html;
}

function renderChange(change: ResourceChange): string {
  const rid = change.resource_id;
  const gvk = rid.group
    ? `${rid.group}/${rid.version}/${rid.kind}`
    : `${rid.version}/${rid.kind}`;
  const fullName = rid.namespace
    ? `${rid.namespace}/${rid.name}`
    : rid.name;

  const statusClass = `badge-${change.status}`;

  let html = `
    <details class="resource" open>
      <summary class="resource-header">
        <span class="badge ${statusClass}">${change.status.toUpperCase()}</span>
        <span class="resource-kind">${escapeHtml(gvk)}</span>
        <span class="resource-name">${escapeHtml(fullName)}</span>
        <span class="diff-stats">
          <span class="additions">+${change.lines_added}</span>
          <span class="deletions">-${change.lines_removed}</span>
        </span>
      </summary>`;

  if (change.diff_text) {
    html += `<div class="diff-block"><table class="diff-table">`;
    const lines = change.diff_text.split("\n");
    for (const line of lines) {
      if (!line) continue;
      const cls = line.startsWith("+")
        ? "diff-add"
        : line.startsWith("-")
          ? "diff-del"
          : line.startsWith("@@")
            ? "diff-hunk"
            : "diff-ctx";
      html += `<tr class="${cls}"><td class="diff-sign">${escapeHtml(line.charAt(0) || " ")}</td><td class="diff-content">${escapeHtml(line.substring(1))}</td></tr>`;
    }
    html += `</table></div>`;
  }

  html += `</details>`;
  return html;
}

function statusBar(filePath: string, extra = ""): string {
  return `<div class="status-bar">
      <span class="file-path">${escapeHtml(basename(filePath))}</span>
      ${extra}
      <button class="refresh-btn" title="Refresh">&#x21bb; Refresh</button>
    </div>`;
}

function basename(filePath: string): string {
  return filePath.split("/").pop() || filePath;
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
