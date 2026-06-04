import * as vscode from "vscode";
import {
  LanguageClient,
  LanguageClientOptions,
  ServerOptions,
} from "vscode-languageclient/node";

let client: LanguageClient | undefined;

export async function startLspClient(
  context: vscode.ExtensionContext
): Promise<void> {
  const config = vscode.workspace.getConfiguration("kdrift");
  if (!config.get<boolean>("lsp.enabled", true)) {
    return;
  }

  const binary = config.get<string>("binaryPath", "kdrift");
  const debug = config.get<boolean>("lsp.debug", false);
  const args = debug ? ["lsp", "--debug"] : ["lsp"];

  const serverOptions: ServerOptions = { command: binary, args };

  const clientOptions: LanguageClientOptions = {
    documentSelector: [
      { scheme: "file", language: "yaml" },
      { scheme: "file", pattern: "**/*.yml" },
    ],
  };

  client = new LanguageClient(
    "kdrift",
    "kdrift Language Server",
    serverOptions,
    clientOptions
  );

  context.subscriptions.push(client);
  await client.start();
}

export async function stopLspClient(): Promise<void> {
  if (client) {
    await client.stop();
    client = undefined;
  }
}
