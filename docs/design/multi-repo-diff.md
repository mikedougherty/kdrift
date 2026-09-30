# Design: multi-repo diff for out-of-repo helm chart sources

## Problem

kdrift renders an overlay's baseline from a detached git worktree of the
analyzed repository (`repo_root`) only. When an overlay depends on a helm chart
whose source lives in **another local git checkout** — via an absolute
`helmGlobals.chartHome`, or a `chartHome` (relative or default) that is a symlink
escaping the repo — the baseline render resolves that path to the *same live
directory* as the current render. Baseline and current then read identical chart
contents, so drift in the external chart is invisible to `diff`.

Foundation already in place (shipped): the dependency graph tracks each
overlay's out-of-repo chart directories as absolute subtree watches
(`DependencyGraph.external_sources`); `affected`/`discover` map changes under
them to consuming overlays; `diff` currently only warns that it cannot capture
that drift.

Empirically confirmed (kustomize v5.8.1):
- An absolute `chartHome` renders; editing the external chart changes the
  current render but a detached-worktree baseline reads the same live chart
  (drift invisible).
- Copying the overlay to a temp dir and setting `chartHome` to a worktree of the
  external repo at a baseline ref produces a correct baseline (external `1.0.0`
  vs current `2.0.0`).
- When a **base** (not the leaf) declares `helmGlobals.chartHome`, the leaf
  renders through it, and the rewrite must target the **base's** kustomization.

## Goals

- `diff` detects drift originating in a local out-of-repo chart checkout.
- Baseline semantic: **per-repo HEAD-vs-working**. Baseline = (analyzed repo
  A @ `--ref`) + (each external repo B @ B's HEAD). Current = (A working) +
  (B working). `--ref` controls A only.
- Correct with a base-declared `chartHome`, multiple charts, multiple distinct
  external repos feeding one overlay, and the force-one-overlay (`--overlay`)
  path.
- Baseline cache stays correct: never serve a stale baseline when an external
  source's state moved, including sources kdrift cannot pin (non-git dirs).

## Scope note (changed after design review)

Because the baseline fix is an unconditional **set** of `helmGlobals.chartHome`
(see Design §3), it handles every local-checkout form uniformly: absolute
`chartHome`, a symlinked `chartHome` escaping the repo, and a relative
`../`-escape `chartHome` (discover already classifies all three as absolute
external subtrees). So relative-escape, previously deferred, is covered here for
near-zero extra cost. Issue #16 narrows to **git-submodule** chart sources only
(a genuinely different mechanism: submodule change detection + submodule
checkout).

## Non-goals (deferred, tracked separately)

- Per-repo baseline ref override (#14 follow-up).
- `--watch` monitoring of out-of-repo dirs (#15).
- Git-submodule chart sources (#16, narrowed).
- Remote (non-local) unpinned-upstream polling (#17).

## Design

### 1. discover.py — record what the rewrite needs

Add a typed accessor returning, per external chart declaration:

- `declaring_kust`: repo-relative dir of the kustomization that declared the
  `helmCharts` + `chartHome` (the base in the base-declares case).
- `chart_home_abs`: the resolved absolute chart-home directory
  (`<chartHome>`, the parent of `<chartHome>/<name>`).

`external_chart_refs() -> list[ExternalChartRef]`, deduplicated per
(declaring_kust, chart_home_abs). discover does NOT call git (stays fast and
git-free); resolving external repo roots is the pipeline's job.

### 2. git.py — reuse + small additions

`Worktree(ref, repo_root=<external>)`, `changed_files(repo_root=...)`,
`resolve_ref(repo_root=...)` already accept a repo root. Add:

- `find_repo_root_or_none(path)` — external chart may sit in a non-git dir;
  return `None` rather than raise.
- Keep `Worktree.__exit__`'s warn-on-cleanup-failure, but callers manage
  multiple worktrees with `contextlib.ExitStack` (see §3 / failure modes).

### 3. pipeline.py — cross-repo change gate + baseline rewrite

**Resolve external repos once per run.** From `external_chart_refs`, resolve
each `chart_home_abs` to `(repo_root, HEAD_sha)` via `find_repo_root_or_none`.
Classify each external ref into:
- **pinnable**: resolves to a git repo B where `B != A` and a worktree can be
  created. Diffable.
- **un-pinnable**: non-git dir, OR resolves back into A (`B == A`), OR worktree
  creation fails (e.g. read-only `.git`). NOT diffable — see cache + degrade
  rules below. Explicitly guard `B != repo_root` here; do not rely solely on
  discover's classification.

Cache these resolutions for the whole run (no per-chart re-shelling).

**Ordering (must change from today).** Build the graph and compute the external
resolutions, then the union change gate, **before** the `if not changed`
early-return:
1. Build graph; get `external_chart_refs`; resolve external repos.
2. `changed` = union of `git.changed_files(A)` and, for each pinnable external
   repo B, `git.changed_files(B)` mapped to absolute (`B_root / path`).
3. Only now apply `if not changed:` (with the existing external-source caveat
   for un-pinnable sources).
4. `affected = graph.affected_overlays(changed)`.

**overlay_filter (`--overlay`) path must also gate.** Today it short-circuits
before `affected_overlays`. It must still run external detection for the forced
overlay so the rewrite applies; otherwise the one-overlay force path silently
reads B live. Resolve the forced overlay's external refs and feed them into the
baseline render the same way.

**Baseline render (working-tree-vs-ref).** For each affected overlay whose
declaring kustomizations include pinnable external refs:
1. With a single `contextlib.ExitStack`, create one worktree per distinct
   pinnable external repo B at B@HEAD (dedup by repo root; one worktree per B,
   not per chart). All `__enter__`s happen before any render; the ExitStack
   guarantees cleanup even on mid-setup failure.
2. In A's ephemeral baseline worktree, for each participating external ref,
   **set** `helmGlobals.chartHome` on the declaring kustomization to
   `<B-worktree>/<rel>` where `rel = chart_home_abs.relative_to(B_root)`. This
   is an unconditional set/create of the field via the YAML loader+dumper
   (round-trip, not a text substitution and not a from→to replace), so it works
   whether the original value was absolute, a symlink, relative, or absent.
   Guard the `relative_to` with try/except; if it fails, treat the ref as
   un-pinnable (warn, no rewrite).
3. Render the overlay from A's baseline worktree.

Current render is unchanged (reads A + B live).

**Un-pinnable / degrade.** For any overlay with an un-pinnable external ref:
do NOT rewrite (can't), do NOT cache its baseline (see cache), and emit the
PR1-style warning naming the source and why (non-git / read-only / same-repo).
The diff for that overlay still runs (it just can't see external drift) — never
crash the whole diff.

**Cache key + un-pinnable interaction.**
- `render.cache_key` gains an OPTIONAL `external` arg: a sorted list of
  `<B_root>@<HEAD_sha>` for the overlay's pinnable external repos. Guard it
  exactly like the existing `env` arg (`if external:`), so an empty external set
  produces a byte-identical key to today — no cache-wide invalidation for
  single-repo repos on upgrade.
- If an overlay has ANY un-pinnable external ref, its baseline read a live
  external dir whose state cannot be keyed. **Do not cache that baseline**
  (bypass `set_cached_render` / `get_cached_render` for that overlay). Serving a
  cached baseline that embedded a live read is what fabricates drift on the next
  run.

**ref-vs-ref (A..B) path.** External repos have no ref in A's history, so both
sides use B@HEAD — implemented via the same rewrite on BOTH the base and target
A-worktrees (not a live read). This keeps the external contribution identical on
both sides (no spurious external drift for a two-committed-A comparison) AND
makes the target-render cache honest: the target render is cached (today at
pipeline `set_cached_render(cache_key(resolved_target, ...))`), so its key must
carry the same `external` list — otherwise a later working-tree run with
`ref == that target` gets a poisoned hit from a render that actually read B
live. Un-pinnable refs here follow the same no-cache rule.

### 4. models.py

`ExternalChartRef(declaring_kust: Path, chart_home_abs: Path)` — frozen model
for the discover→pipeline handoff.

## Edge cases

- **Non-git external dir** → un-pinnable: warn, diff without external baseline,
  don't cache, don't crash.
- **Read-only external `.git`** (worktree add fails) → caught, un-pinnable
  degrade path (NOT an uncaught GitError failing the whole diff).
- **`chart_home_abs` resolves back into A** (bind mount / sibling symlink) →
  guarded `B == A` → treated un-pinnable; never spawn a second A worktree.
- **`relative_to(B_root)` raises** (resolve/mount mismatch) → caught →
  un-pinnable degrade.
- **Multiple external repos feeding one overlay** → one worktree each (ExitStack),
  multiple chartHome sets in the same A baseline worktree.
- **Base declares chartHome, several leaves include it** → set the base
  kustomization once per baseline worktree; each leaf renders through it.
- **`chart_home_abs == B_root`** (chartHome at repo top) → `rel == "."` → set to
  `<B-worktree>`.
- **Temporal mismatch** `--ref HEAD~20` builds old-A against B@HEAD; if old-A's
  chart usage is incompatible with current B, the baseline build fails and
  surfaces as `"baseline build failed (pre-existing)"`. Document this signature;
  it is a synthetic old-A/new-B combination, not a real prior state. (Fixed
  properly only by per-repo refs, #14 follow-up.)

## Failure modes (from adversarial review)

- **Stale/fabricated drift via cache:** addressed by (a) keying pinnable
  external SHAs and (b) never caching baselines with un-pinnable external reads.
- **Worktree lifecycle:** single `ExitStack`; all external worktrees created
  before render; guaranteed teardown on exception. Note the blast radius: kdrift
  now writes worktree metadata into the **external** repo's `.git/worktrees/`.
  This is a deliberate, documented expansion of the "read-only git operations
  only" principle (the working trees are read-only; worktree bookkeeping is a
  write into the referenced repo's `.git`). Failed cleanup accumulates there —
  ExitStack + the existing warn-on-remove-failure mitigate but don't eliminate.
- **Concurrency (corrected):** the baseline render loop is SERIAL today; only the
  current render runs in a ThreadPoolExecutor and it reads live (no worktrees),
  so there is no parallel-baseline race to guard. The real, pre-existing risk is
  the global on-disk cache: `set_cached_render` is a bare `write_text`, not
  atomic, and concurrent runs (CLI + LSP + MCP) can interleave. This design
  multiplies cache keys/writes and mildly worsens it. Mitigation in scope:
  make `set_cached_render` atomic (write to a temp file in the same dir + rename).
- **Path aliasing:** compare `chart_home_abs` and B's changed files in the same
  resolved space (both `.resolve()`d) to avoid `/tmp` vs `/private/tmp` misses.

## Testing

- Unit: `external_chart_refs` (leaf-declared, base-declared, multiple charts,
  multiple repos); the YAML chartHome-set helper (absent field → created;
  existing → replaced); `cache_key` includes external SHAs when present AND is
  byte-identical to today when the external set is empty (explicit regression
  assertion); `B == A` and non-git → un-pinnable classification.
- Integration (self-contained, extends the existing harness): repo A + external
  chart repo B; edit B's chart → A's overlay diff shows drift; clean B → no
  drift; base-declared chartHome works; **symlink-escape** chartHome (not just
  absolute); un-pinnable (non-git) external dir → warning + no crash + baseline
  not cached.
- Regression: single-repo diffs unchanged (no external refs → no new worktrees,
  identical output, byte-identical cache keys).

## Rollout

Single feature branch, conventional `feat:`. No flag — behavior activates only
when an overlay actually has out-of-repo chart sources; repos without them take
the same code path with an empty external set (identical output and cache keys,
asserted by test). Adversarial code review before merge.
