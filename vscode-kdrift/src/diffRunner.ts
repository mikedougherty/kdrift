import * as vscode from "vscode";
import { execFile } from "child_process";
import { promisify } from "util";
import type { DiffResult } from "./types";

const execFileAsync = promisify(execFile);

interface ExecResult {
  stdout: string;
  stderr: string;
  code: number | null;
  killed: boolean;
}

function runCommand(
  binary: string,
  args: string[],
  timeout: number
): Promise<ExecResult> {
  return new Promise((resolve) => {
    const proc = execFile(
      binary,
      args,
      { timeout, maxBuffer: 10 * 1024 * 1024 },
      (error, stdout, stderr) => {
        const killed = error?.killed ?? false;
        const code =
          error && "code" in error && typeof error.code === "number"
            ? error.code
            : error
              ? 1
              : 0;
        resolve({ stdout: stdout ?? "", stderr: stderr ?? "", code, killed });
      }
    );
    proc.unref();
  });
}

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

    const result = await runCommand(binary, args, 60_000);

    if (result.killed) {
      throw new Error(
        "kdrift timed out (60s). The repo may have too many overlays."
      );
    }

    if (result.stdout) {
      try {
        return JSON.parse(result.stdout) as DiffResult;
      } catch {
        // stdout wasn't valid JSON, fall through to error
      }
    }

    if (result.code === 0) {
      return { ref: "", target_ref: null, overlays: [], errors: [] };
    }

    const stderr = result.stderr.trim();
    throw new Error(
      stderr || `kdrift exited with code ${result.code}. Run 'kdrift diff' in the terminal for details.`
    );
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
