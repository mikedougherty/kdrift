# Design: resolve helm dependencies in the baseline worktree

Tracks [#23]. Follow-up to the multi-repo diff (`docs/design/multi-repo-diff.md`).

## Problem

For an out-of-repo chart, kdrift renders the baseline from a fresh
`git worktree` of the external repo at HEAD, which contains only committed
files. The common helm convention is to **gitignore** the resolved subchart
tarballs (`.gitignore: */charts/**`): `Chart.yaml` + `Chart.lock` are committed,
but `charts/<dep>-<ver>.tgz` is produced on demand by `helm dependency build`.

So the baseline worktree lacks the subcharts and `kustomize build --enable-helm`
fails there, while the working-tree candidate (tarballs present on disk) renders
fine. Result: a spurious `"baseline build failed (pre-existing)"` and an empty
diff on a chart that is actually fine.

Confirmed against a real `helm-charts` ↔ `argo-config` scenario (kdrift
0.1.6rc1): force-committing the missing `foundry-engine-0.2.0.tgz` into the
external repo's HEAD made the baseline render and produced the correct
25-resource diff. Dependency presence in the worktree is the only missing piece.

## Goals

- A baseline render of an out-of-repo chart with committed `Chart.yaml`/
  `Chart.lock` but gitignored/absent `charts/` succeeds and produces a correct,
  non-empty diff.
- The baseline reflects the chart's dependencies **as of the baseline ref** when
  it can (primary path), so a diff that includes a dependency bump shows it.
- Resolution does not re-run on every diff when it is safe to cache (see
  Caching), and never silently reports clean.

## Non-goals

- Resolving deps for in-repo charts whose `charts/` is committed (already works,
  and is unaffected by this change).
- Fixing the opaque failure message — that is [#24] (surface stderr). This design
  must not *regress* diagnostics and leans on #24 for the final-failure message.
- The `--chart-home` convenience flag / baseline-ref docs — that is [#25].

## Three baseline states (the core model)

An affected overlay's baseline now falls into exactly one of three states. Only
the first is cached.

| State | When | Cached? | User signal |
|-------|------|---------|-------------|
| **ref-resolved** | deps already present in the worktree (committed `charts/`), or `helm dependency build` succeeded against the committed `Chart.lock` | yes | none |
| **worktree-resolved** (fallback) | `helm dependency build` failed (offline/auth), deps copied from the live checkout | **no** | warning: baseline deps came from the working tree, not `<ref>` |
| **unresolved** | neither worked | no (render fails) | baseline error (enriched by #24) |

Critically, **worktree-resolved is NOT the same as the multi-repo `unpinnable`
state.** `unpinnable` means "not diffed at all"; worktree-resolved means "diffed,
but with working-tree deps, so don't cache and note the caveat." Implementation
must carry a distinct per-declaring-kust set (e.g. `plan.deps_from_worktree`),
separate from `plan.unpinnable`, so:

- `_overlay_external` computes `cacheable = (no unpinnable ref) AND (no
  deps_from_worktree ref)`.
- `_unpinnable_warnings` is unchanged (fires only for truly-undiffed overlays).
- A **new** warning function fires for `deps_from_worktree` overlays with the
  correct message. The old "out-of-repo chart drift not captured" text must NOT
  appear for a fallback overlay (its drift *was* captured).

## Design

### Where it hooks in — once per external worktree

`_diff_working_tree_vs_ref` / `_diff_ref_vs_ref` open the A worktree(s) + one
worktree per external repo (ExitStack), then `_rewrite_chart_homes` points each
declaring kustomization's `chartHome` at the B worktree. Add **one** dependency-
resolution step, keyed per **B worktree** (not per A-rewrite): after the B
worktrees exist and chartHome is rewritten, resolve deps once in each B worktree.
In `_diff_ref_vs_ref` the base and target A-worktrees share the same
`b_worktrees` dict and the same rewritten chartHome target, so resolution runs
once on the shared B worktree, not once per A-rewrite.

Current render reads the live working tree (resolved `charts/` already on disk),
so the candidate path is unchanged.

**Strict ordering (load-bearing).** Resolution must run **after**
`_rewrite_chart_homes` + `_demote_unrewritten` and **before** the per-overlay
render loop — never lazily inside the loop. This guarantees `plan.unpinnable` and
`plan.deps_from_worktree` are disjoint per declaring-kust (demotion finished
first) and that `_overlay_external`'s `cacheable` read in the loop sees the fully
populated `deps_from_worktree` set. The `deps_from_worktree` **warning** is
emitted in `run_diff` after the diff helper returns (exactly like
`_unpinnable_warnings` today), reading the set the helper populated — not inside
the helper.

### Which charts

Resolve deps only for the charts an affected overlay actually inflates —
`<chartHome>/<name>` for each referenced `helmCharts[].name`. Chart names are
parsed in discover (`_collect_helm_refs`); extend `ExternalChartRef` to carry the
referenced chart names so the pipeline targets exactly those dirs, rather than
scanning every chart dir under `chartHome` (which could build an unrelated chart
and fail on its registry).

### Resolution per referenced chart

For each referenced chart dir `D = <B-worktree>/<rel>/<name>`:

1. **Confinement guard (data safety, hard stop).** Require both `D.resolve()`
   **and** the write targets `(D/charts).resolve()` and `(D/Chart.lock).resolve()`
   (their nearest existing ancestor if absent) to live inside
   `<B-worktree>.resolve()`. If `D`, an ancestor of it, or a committed `charts`/
   `Chart.lock` inside it is a symlink that escapes the worktree, this chart is
   **skip + warn only** — do NOT run `helm dependency build` AND do NOT copy into
   `D`. Both would write through the escaping link into the live checkout. (The
   copy fallback is explicitly *not* an option for an escaping `D`; it writes into
   `D` just as the build does.) Skip → the baseline may fail for this chart
   (unresolved), which is acceptable; a write through a symlink into the user's
   tree is not.
2. **Skip if already complete / no deps.** If `D/Chart.yaml` declares no
   `dependencies:` → nothing to do (no-deps case; ref-resolved, cached).
   Otherwise, determine the required set from `D/Chart.lock` when present (its
   pinned concrete `name`+`version` is exactly what `helm dependency build`
   satisfies, and avoids a range vs concrete-version mismatch when `Chart.yaml`
   uses `^`/`~` ranges); fall back to `Chart.yaml` deps if there is no lock. It is
   "complete" only when every required dependency has a matching
   `D/charts/<name>-<version>.tgz` (or an unpacked `D/charts/<name>/`) — match the
   required deps against present artifacts, do not just test that `charts/` is
   non-empty. A **partial** `charts/` (some tarballs present, not all) counts as
   incomplete → resolve. Complete → ref-resolved, cached.
3. **Primary — `helm dependency build D`.** Use the same helm binary kustomize
   uses and the injected `env`; it reads the committed `Chart.lock` and resolves
   baseline-ref deps. Success → ref-resolved (cacheable). See the helm contract.
4. **Fallback — copy from live** (only when `D` passed the confinement guard).
   If primary fails, first remove any partial `D/charts/` and `D/tmpcharts/` the
   failed/timed-out build left behind, then copy `<live chartHome>/<name>/charts/`
   (+ `Chart.lock`) from the live checkout the chartHome originally pointed at,
   into `D` (`dirs_exist_ok`). Success → worktree-resolved (non-cacheable +
   warning).
5. **Neither** → unresolved; leave `kustomize build` to fail; overlay reports the
   error (not clean).

### Helm invocation contract

- **Binary**: the helm the project configures for kustomize (`--helm-command`,
  default `helm`); reuse it for `helm dependency build`.
- **Config/cache**: use the ambient helm config (`$HELM_CONFIG_HOME`/
  `$HELM_REPOSITORY_CACHE`, default `~/.config/helm`, `~/.cache/helm`) plus the
  project's injected `env`. This is deliberate: in a dev loop the user already ran
  `helm dependency build` / `helm repo add` / `helm registry login` to build the
  live `charts/`, so the ambient config can resolve the same deps — which is
  exactly why the primary path works in practice. It also means the baseline
  reads ambient helm state that is not in kdrift's cache key (see Caching).
- **Auth**: classic HTTP repos must already be `helm repo add`-ed; OCI auth lives
  in helm's `config.json` via `helm registry login` (not `HELM_REGISTRY_TOKEN`).
  kdrift does not manage auth — if the ambient config can't resolve a dep, the
  primary path fails and we fall back. Honest expectation: primary succeeds for
  the dev-loop case (deps already fetchable locally); fallback covers
  offline/CI-without-auth; neither → a named error via #24.
- **Timeout**: `helm dependency build` can hit the network, so it runs with a
  bounded timeout (configurable; sensible default). On timeout → fallback. This
  also protects `--watch`/LSP from hanging on an unreachable registry. (The
  existing `kustomize build` calls have no timeout; adding one there is out of
  scope but noted.)

### Caching

The baseline render cache keys on external repo HEAD (+ overlay + kustomize
version + args). **Add the helm binary version to the key** (`cache_key` currently
includes the kustomize version but not helm's): this design makes `helm` responsible
for resolving deps into the cached baseline, so a helm upgrade that changes
resolution/packaging must invalidate it. Guard it like the other optional key
inputs so single-repo / no-helm renders keep a byte-identical key.

- **ref-resolved from committed `charts/`** (in-repo convention): tarball bytes
  are in git at that HEAD → the key fully pins the output. No change, fully safe.
- **ref-resolved via `helm dependency build`** (gitignored deps): **precondition
  — the registry serves immutable content per pinned version.** `Chart.lock`
  pins exact versions and `helm dependency build` never re-resolves ranges, so
  with an immutable registry the resolved bytes are a function of the committed
  lock (covered by the HEAD key). On a *mutable* registry (a version overwritten
  in place) a cached baseline could go stale until the external HEAD or args
  change. This is a known, documented limitation (internal registries are
  immutable-per-version by convention). A future hardening (out of scope) could
  fold a content fingerprint of the resolved `charts/` into the key; we do not do
  that now because computing it requires the fetch anyway.
- **worktree-resolved (fallback)**: deps came from the working tree, not keyed —
  **not cached** (the `deps_from_worktree` state forces `cacheable=False` on both
  read and write, reusing `_render_with_cache`'s existing gating).

### Warnings

- worktree-resolved: `overlay '<path>': baseline helm dependencies resolved from
  the working tree, not <ref> (registry unreachable?); dependency-version drift
  is not captured`.
- unresolved: baseline fails as before (no false clean); message enriched by #24.

## Edge cases

- **No dependencies / committed `charts/`** → skip resolution, unchanged.
- **Newly-added chart** (chart dir absent at the baseline ref): the worktree has
  no `Chart.yaml`, so neither resolution nor fallback applies; the baseline
  `kustomize build` fails → "baseline build failed (pre-existing)". Acceptable
  (never reports clean) but reproduces the opaque message; leans on #24 for a
  clearer signal. Documented, not fixed here.
- **`rel == "."`** (chartHome at repo root) → chart dir is `<B-worktree>/<name>`;
  handled by the same path logic.
- **Nested subchart deps** → `helm dependency build` resolves transitively; the
  copy fallback copies the resolved tree wholesale.
- **Stale `Chart.lock` vs `Chart.yaml`** → `helm dependency build` errors →
  fallback, or unresolved with the stderr (#24).

## Failure modes

- **Escaping symlink chart dir** → confinement guard prevents any write through
  the link (never mutates the live checkout); routed to **skip only** (never the
  copy fallback — `copytree` would write through the escaping link too).
- **Network/registry failure or timeout** in primary → fallback (non-cacheable +
  warn); never poisons the cache, never hangs unboundedly.
- **Mutable registry** → documented caching precondition above.
- **Concurrent runs** (CLI + LSP + MCP) share `$HELM_REPOSITORY_CACHE`; helm's
  cache locking is weak, so simultaneous `helm dependency build` could in theory
  race. Low probability; worktrees are per-run so only the shared helm cache is
  shared. Acknowledged; not mitigated now.
- **Writes into the ephemeral worktree** (`charts/`, `Chart.lock`, `tmpcharts/`)
  are torn down by the ExitStack; they never touch the live checkout (guard
  above).

## Testing

- Unit: the needs-resolution predicate (deps declared AND `charts/` incomplete);
  the confinement guard (escaping symlink → not built in place); chart-name
  targeting; the fallback-copy helper; `deps_from_worktree` forces
  `cacheable=False`; the fallback warning text (and that the unpinnable warning
  does NOT fire for it).
- Integration (kustomize+helm guarded, extends the two-repo harness): external
  chart with committed `Chart.yaml`/`Chart.lock` + a gitignored subchart tarball
  (a tiny local file:// or path dependency to avoid network) → baseline resolves
  → non-empty correct diff; clean chart → no drift; simulate primary failure
  (point the dep at an unreachable registry) → fallback copy → correct diff + the
  working-tree-deps warning + baseline not cached.
- Regression: in-repo chart with committed `charts/` renders identically (no dep
  step runs); single-repo diffs unchanged; byte-identical cache keys when no
  external deps.

## Rollout

Single feature branch, conventional `feat:`. Behavior activates only when an
external chart declares unresolved dependencies. Adversarial design review (this
doc) → implement → adversarial code review before merge. Ship under continued
`0.1.6rcN` pre-releases until #23 is proven, then cut stable 0.1.6.
