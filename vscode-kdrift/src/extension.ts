import * as vscode from "vscode";
import { DiffRunner } from "./diffRunner";
import { DriftPreviewProvider } from "./driftPreviewProvider";
import { startLspClient, stopLspClient } from "./lspClient";

let previewProvider: DriftPreviewProvider | undefined;

export async function activate(
  context: vscode.ExtensionContext
): Promise<void> {
  const diffRunner = new DiffRunner();

  const available = await diffRunner.checkBinary();
  if (!available) {
    return;
  }

  await startLspClient(context);

  previewProvider = new DriftPreviewProvider(context.extensionUri, diffRunner);

  context.subscriptions.push(
    vscode.commands.registerCommand("kdrift.openPreview", () => {
      previewProvider?.openPreview(false);
    }),
    vscode.commands.registerCommand("kdrift.openLockedPreview", () => {
      previewProvider?.openPreview(true);
    }),
    vscode.commands.registerCommand("kdrift.refreshPreview", () => {
      previewProvider?.refresh();
    }),
    vscode.workspace.onDidSaveTextDocument((document) => {
      previewProvider?.onDocumentSaved(document);
    }),
    vscode.window.onDidChangeActiveTextEditor((editor) => {
      previewProvider?.onActiveEditorChanged(editor);
    }),
    { dispose: () => previewProvider?.dispose() }
  );
}

export async function deactivate(): Promise<void> {
  previewProvider?.dispose();
  await stopLspClient();
}
