import * as vscode from "vscode";
import { DiffRunner } from "./diffRunner";
import type { DiffResult, ToWebviewMessage, FromWebviewMessage } from "./types";

interface PreviewState {
  result: DiffResult;
  filePath: string;
}

export class DriftPreviewProvider {
  private panel: vscode.WebviewPanel | undefined;
  private locked = false;
  private trackedUri: string | undefined;
  private lastState: PreviewState | undefined;
  private debounceTimer: ReturnType<typeof setTimeout> | undefined;
  private disposables: vscode.Disposable[] = [];

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly diffRunner: DiffRunner
  ) {}

  openPreview(locked: boolean): void {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
      vscode.window.showWarningMessage("No active editor");
      return;
    }

    this.locked = locked;
    this.trackedUri = editor.document.uri.toString();

    if (this.panel) {
      this.panel.reveal(vscode.ViewColumn.Beside);
    } else {
      this.createPanel();
    }

    this.updatePreview(editor.document.uri.fsPath);
  }

  refresh(): void {
    if (this.lastState) {
      this.updatePreview(this.lastState.filePath);
    }
  }

  onDocumentSaved(document: vscode.TextDocument): void {
    if (!this.panel) {
      return;
    }

    const fsPath = document.uri.fsPath;
    if (!fsPath.endsWith(".yaml") && !fsPath.endsWith(".yml") && !fsPath.endsWith(".json")) {
      return;
    }

    if (this.locked && document.uri.toString() !== this.trackedUri) {
      return;
    }

    const config = vscode.workspace.getConfiguration("kdrift");
    const debounceMs = config.get<number>("debounceInterval", 500);

    if (this.debounceTimer) {
      clearTimeout(this.debounceTimer);
    }

    this.debounceTimer = setTimeout(() => {
      this.updatePreview(document.uri.fsPath);
    }, debounceMs);
  }

  onActiveEditorChanged(editor: vscode.TextEditor | undefined): void {
    if (!this.panel || this.locked || !editor) {
      return;
    }

    this.trackedUri = editor.document.uri.toString();
    this.updatePreview(editor.document.uri.fsPath);
  }

  dispose(): void {
    if (this.debounceTimer) {
      clearTimeout(this.debounceTimer);
    }
    this.panel?.dispose();
    for (const d of this.disposables) {
      d.dispose();
    }
    this.disposables = [];
  }

  private createPanel(): void {
    this.panel = vscode.window.createWebviewPanel(
      "kdrift.preview",
      "kdrift: Drift Preview",
      vscode.ViewColumn.Beside,
      {
        enableScripts: true,
        localResourceRoots: [
          vscode.Uri.joinPath(this.extensionUri, "dist", "webview"),
        ],
      }
    );

    this.panel.onDidDispose(
      () => {
        this.panel = undefined;
        this.lastState = undefined;
        if (this.debounceTimer) {
          clearTimeout(this.debounceTimer);
        }
      },
      null,
      this.disposables
    );

    this.panel.onDidChangeViewState(
      (e) => {
        if (e.webviewPanel.visible && this.lastState) {
          this.postMessage({
            type: "update",
            data: this.lastState.result,
            filePath: this.lastState.filePath,
          });
        }
      },
      null,
      this.disposables
    );

    this.panel.webview.onDidReceiveMessage(
      (msg: FromWebviewMessage) => {
        switch (msg.type) {
          case "refresh":
            this.refresh();
            break;
          case "openFile":
            vscode.window.showTextDocument(vscode.Uri.file(msg.path));
            break;
          case "ready":
            if (this.lastState) {
              this.postMessage({
                type: "update",
                data: this.lastState.result,
                filePath: this.lastState.filePath,
              });
            }
            break;
        }
      },
      null,
      this.disposables
    );

    this.panel.webview.html = this.getWebviewHtml(this.panel.webview);
  }

  private async updatePreview(filePath: string): Promise<void> {
    if (!this.panel) {
      return;
    }

    this.postMessage({ type: "loading", filePath });

    try {
      const result = await this.diffRunner.runDiff(filePath);
      this.lastState = { result, filePath };

      const hasChanges = result.overlays.some((o) => o.changes.length > 0);
      if (!hasChanges && result.errors.length === 0) {
        this.postMessage({ type: "noChanges", filePath });
      } else {
        this.postMessage({ type: "update", data: result, filePath });
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      this.postMessage({ type: "error", message, filePath });
    }
  }

  private postMessage(msg: ToWebviewMessage): void {
    this.panel?.webview.postMessage(msg);
  }

  private getWebviewHtml(webview: vscode.Webview): string {
    const scriptUri = webview.asWebviewUri(
      vscode.Uri.joinPath(this.extensionUri, "dist", "webview", "preview.js")
    );
    const styleUri = webview.asWebviewUri(
      vscode.Uri.joinPath(this.extensionUri, "dist", "webview", "styles.css")
    );
    const nonce = getNonce();

    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy"
    content="default-src 'none'; style-src ${webview.cspSource}; script-src 'nonce-${nonce}';">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link href="${styleUri}" rel="stylesheet">
  <title>kdrift Preview</title>
</head>
<body>
  <div id="root">
    <div class="empty-state">
      <p>Save a file to see drift preview.</p>
    </div>
  </div>
  <script nonce="${nonce}" src="${scriptUri}"></script>
</body>
</html>`;
  }
}

function getNonce(): string {
  const chars =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  let result = "";
  for (let i = 0; i < 32; i++) {
    result += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return result;
}
