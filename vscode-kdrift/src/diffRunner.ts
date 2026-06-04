import * as vscode from "vscode";
import { execFile } from "child_process";
import { promisify } from "util";
import type { DiffResult } from "./types";

const execFileAsync = promisify(execFile);

export class DiffRunner {
  private repoRootCache = new Map<string, string>();

  async runDiff(filePath: string): Promise<DiffResult> {
    const repoRoot = await this.findRepoRoot(filePath);
    const config = vscode.workspace.getConfiguration("kdrift");
    const binary = config.get<string>("binaryPath", "kdrift");
    const ref = config.get<string>("ref", "HEAD");

    const args = ["diff", "--format", "json", "-C", repoRoot, "--ref", ref];

    const kustomizeArgs = config.get<string[]>("kustomizeArgs", []);
    if (kustomizeArgs.length > 0) {
      for (const arg of kustomizeArgs) {
        args.push("--kustomize-arg", arg);
      }
    }

    const relativePath = filePath.startsWith(repoRoot)
      ? filePath.substring(repoRoot.length + 1)
      : filePath;
    args.push(relativePath);

    try {
      const { stdout } = await execFileAsync(binary, args, {
        timeout: 60_000,
        maxBuffer: 10 * 1024 * 1024,
      });
      return JSON.parse(stdout) as DiffResult;
    } catch (err: unknown) {
      const execErr = err as { stderr?: string; code?: number; killed?: boolean };
      if (execErr.killed) {
        throw new Error("kdrift timed out (60s). The repo may have too many overlays.");
      }
      const stderr = execErr.stderr?.trim();
      throw new Error(stderr || `kdrift exited with code ${execErr.code ?? "unknown"}`);
    }
  }

  async checkBinary(): Promise<boolean> {
    const config = vscode.workspace.getConfiguration("kdrift");
    const binary = config.get<string>("binaryPath", "kdrift");
    try {
      await execFileAsync(binary, ["--help"], { timeout: 5_000 });
      return true;
    } catch {
      vscode.window.showErrorMessage(
        `kdrift binary not found at "${binary}". Install with: uv tool install kdrift`
      );
      return false;
    }
  }

  private async findRepoRoot(filePath: string): Promise<string> {
    const dir = filePath.substring(0, filePath.lastIndexOf("/"));
    const cached = this.repoRootCache.get(dir);
    if (cached) {
      return cached;
    }

    const { stdout } = await execFileAsync(
      "git",
      ["rev-parse", "--show-toplevel"],
      { cwd: dir, timeout: 5_000 }
    );
    const root = stdout.trim();
    this.repoRootCache.set(dir, root);
    return root;
  }
}
