export interface ResourceId {
  group: string;
  version: string;
  kind: string;
  namespace: string;
  name: string;
}

export interface ResourceChange {
  resource_id: ResourceId;
  status: "modified" | "added" | "removed";
  diff_text: string;
  lines_added: number;
  lines_removed: number;
}

export interface OverlayResult {
  path: string;
  changes: ResourceChange[];
  error: string | null;
}

export interface DiffResult {
  ref: string;
  target_ref: string | null;
  overlays: OverlayResult[];
  errors: string[];
}

export type ToWebviewMessage =
  | { type: "update"; data: DiffResult; filePath: string }
  | { type: "loading"; filePath: string }
  | { type: "error"; message: string; filePath: string }
  | { type: "noChanges"; filePath: string };

export type FromWebviewMessage =
  | { type: "refresh" }
  | { type: "openFile"; path: string }
  | { type: "ready" };
