# Compiler Changes for v3

Reviewer-facing walkthrough of the compiler side of the v3 redesign. Each
section follows the same arc: what the compiler does today, where that
falls short, and what v3 changes. Runtime data structures and the
time-naming vocabulary live in `data_model.md`; the byte contract lives in
`blob_format.md`; decoder-side responsibilities live in `decoder.md`.

## 1. Overview

Today's compiler (`compiler/elements/compiler.py`) resolves times
globally, then runs a per-strip pipeline in `_compile_strip()`:

```
time resolution (global)
→ per strip: _infer_layers → _pack_buffers →
  _resolve_and_validate_source_layers → _compute_required_starts →
  _find_safe_intervals → param resolution → blob.emit_blob
```

Current outputs:

- **v2 blob** (`blob.py`) — header + per-layer `index_map` + per-event
  fixed fields (`anim_type`, `t_start`, `duration`, `source_layer`,
  `remap`/`remap_is_identity`/`remap_length`) + animation-specific
  params; shift events additionally carry a `buffer_id` into the shift
  work pool.
- **CompiledManifest** (`types.py`) — program-level metadata the
  controller keeps out of the blob, most importantly
  `safe_intervals`.

What is missing for the v3 direction:

- `PixelViewSpec` records, `CopyOp` records
- `Program::target_fps`, `Program::requires_sync`
- minimum-width rule on safe intervals
- provenance model that can see past `source_layer`

**Ownership split is unchanged.** v3 keeps
**compiler-proves / controller-gates / runtime-trusts**: the compiler
computes safety metadata, the controller gates reconstruction commands,
the runtime runs without defensive checks. `safe_intervals` stays in
`CompiledManifest`, not the blob — shipping them to the runtime is not
planned. The rest of this doc is about strengthening the compiler's
proof under that unchanged split.

## 2. Safe Intervals and Rejoin

**Background.** An event that reads a preserved source can't safely play
unless that source was actually rendered. v2 already has this problem for
`source_layer` dependencies; v3 keeps the same safety idea but
generalizes it.

**Today.** `_compute_required_starts()` + `_find_safe_intervals()` emit
per-strip intervals that are intersected globally and stored in
`CompiledManifest.safe_intervals`. No minimum-width filter, no headroom
rule. A degenerate `(0.0, 0.0)` sentinel marks the `t_program == 0`
start, sharing the list with the nonzero intervals.

**Limitations.**

1. Without a width filter, the controller can pick a target inside a
   valid interval that is too narrow to render even one frame before the
   next writer lands.
2. Live rejoin has no headroom rule, so a controller landing at
   `interval.end - epsilon` reconstructs state that has no time to
   render.
3. Paused rejoin trusts the paused position. Today that's fine because
   the engine state is intact, but a **fresh device** reconstructing
   paused state (e.g., after a re-LOAD) doesn't inherit buffers — an
   unsafe paused point would then break reconstruction.
4. Because the zero sentinel shares the list with nonzero intervals, a
   future width filter on the list would strip it too, making `START`
   at program start conditional on the first interval's width.

**Proposal.**

1. Compiler filters nonzero candidate intervals narrower than one
   target-frame period (`1 / Program::target_fps`).
2. Compiler preserves the `t_program == 0` sentinel separately from the
   nonzero interval list, so `START` at program start is always safe by
   construction.
3. Controller leaves one target-frame period of headroom when choosing
   any reconstruction target inside an interval.
4. Commands that **reconstruct engine state at a nonzero `t_program`**
   (`JUMP`, live rejoin / re-`LOAD`, `START` at nonzero time) are gated
   on falling inside a safe interval with headroom. `START` at
   `t_program == 0` is unconditional.
5. Paused rejoin for a fresh device uses the same safe-point rule as
   live rejoin, skipping the final `RESUME` until the session resumes.

**How it addresses the gaps.** The width filter plus the headroom rule
guarantee any controller-chosen target can render at least one frame
before the next writer lands, so `Engine::reset` + monotonic
`run_copy_ops_until` remain correct. The zero-start sentinel decouples
startup from rejoin semantics. Paused rejoin now has the same
reconstruction guarantees as live rejoin.

### RESUME is not gated

`Playback::handle_resume` doesn't call `Engine::reset()`. Copy cursor,
layer cursors, and HSVA buffers are intact, so resuming forward from the
paused position is correct regardless of safe-interval position. The
contract relies on the controller using `RESUME` only for
resuming-where-paused; meaningful retiming goes through `JUMP`, which is
gated.

### Live rejoin

- **Playing session**: controller sends `LOAD`, `JUMP(safe_point_t)`,
  then `RESUME(program_start_us)`. The device waits until the clock
  reaches the cursor — effectively zero when the target is the current
  live time.
- **Paused session**: same safe-point rule, no final `RESUME`. The
  device waits at the safe point.
- If there is no future safe interval in the current program segment,
  the controller cannot safely rejoin that device.

### Implications

- Programs with elaborate source chains have shorter safe intervals.
  That is the intended trade-off.
- The controller doesn't validate widths itself; the compiler has
  already filtered them. The controller still enforces the headroom
  rule on every reconstruction target (`JUMP`, live rejoin / re-`LOAD`,
  `START` at nonzero time) — a valid interval isn't enough if the
  target lands near its end.
- Offline render walks frames sequentially from `0`, so safe intervals
  are irrelevant to it.

### Safe intervals vs. sampling cadence

Safe intervals protect **reconstruction** (`JUMP`, rejoin, nonzero
`START`) from landing in states whose rendered sources never existed.
Sampling cadence (§3) is orthogonal: very short events can still be
skipped entirely at a coarse offline step regardless of interval width.

## 3. Target FPS and Sampling

**Background.** Compiler safety, controller gating, and offline render
need a shared cadence. Today they don't have one.

**Today.** No `target_fps` in the DSL, the compiler, or the blob.
Offline render takes an explicit `--fps` (default 50) that is
disconnected from the compiler's safe-interval assumptions, so the two
can diverge.

**Limitation.** Without a cadence carried in the artifact, the width
filter and headroom rule can't be expressed on the compiler side, and
offline render can sample at a different rate than the compiler assumed
when emitting intervals.

**Proposal.** `Program::target_fps`, default 50 Hz. One program-level
value, emitted into every v3 strip blob header for that compiled
program. It drives three decisions:

1. Compiler filters nonzero safe intervals narrower than `1 /
   target_fps`.
2. Controller reserves one target-frame period of headroom on every
   reconstruction target.
3. Offline render uses `target_fps` as its default step cadence; tooling
   may still expose an explicit override.

**Not a decision.** `target_fps` is **not** a firmware LOAD admission
gate. Firmware runs the program regardless and reports render/present
slack so the composer can lower complexity or the declared rate. Owner
presentation loops are documented in `playback.md`.

## 4. Requires Sync Flag

**Background.** Some animations need a common time base across devices;
others don't. The compiler needs to tell the runtime which mode a given
program runs in.

**Today.** No such flag. Synced vs. unsynced is chosen by deployment,
not declared by program content.

**Limitation.** Deployment-level selection can't distinguish animations
that truly need sync from ones that happen to run on a synced device.
Content-level intent is lost.

**Proposal.** `Program::requires_sync` bool. One program-level value,
uniform across all strip artifacts of that compiled program, emitted
into every v3 strip blob header. Read once at `LOAD` and stored on
`Playback`; drives clock-domain selection (`now_remote_us()` vs.
`now_local_us()`) and admission of `START` / `RESUME` / `JUMP`
(rejected while `SyncedClock::is_synced()` is false). Runtime behavior
is detailed in `playback.md`.

**Compiler-emitted, runtime-enforced.** The compiler just carries the
flag; it doesn't know whether the target device will be synced at play
time. That check is on `Playback`.

DSL surface for declaring `requires_sync` is TBD (expected to land as a
program-level constant).

## 5. View-Provenance Graph

**Background.** `required_start_sec` has to look back through the full
chain that feeds a dependent event so the unsafe span covers every
source the event transitively depends on.

**Today.** `_compute_required_starts()` walks one edge type: an event's
explicit `source_layer` → that source event's `required_start_sec`.

**Limitations.**

- No concept of `PixelView`, so the compiler can't reason about storage
  that outlives a single event.
- No concept of **stable-dst preservation**: event A writes V, nothing
  overwrites V before event B reads V. v2 requires an explicit
  `source_layer` link; there is no way to express "V is simply still
  valid."
- No concept of `CopyOp`, so preservation that involves moving content
  between storage locations can't be expressed in a way the provenance
  walk can see.

**Proposal.** Replace the single-edge walk with a two-edge graph:

- **Event → view**: an `AnimationEvent` writes its `dst_pixv_idx`
  during `[event.start, event.start + event.duration)`. The writer of
  that view at any time inside the interval (and forward until the next
  writer) is that event.
- **CopyOp → view**: a copy op writes its `dst_pixv_idx` at time `at`.
  The write transitively depends on the writer of the copy op's
  `src_pixv_idx` at time `at`.

`required_start_sec` for an event `E` with `src_pixv_idx = V`:

1. find the most recent writer to V at any time `<= E.start` (event or
   copy op);
2. if the writer is a copy op, recursively walk to the writer of its
   `src_pixv_idx`;
3. if the writer is an event A, the chain terminates; take A's own
   `required_start_sec` (or `A.start` if A has no source dependencies);
4. set E's `required_start_sec` to the earliest start in the chain.

The same walk applies to `work_pixv_idx` when the work view is
initialized from preserved content (not just used as scratch).

**How it addresses the gaps.** Stable-dst preservation falls out
naturally — if no other event writes V between A and E, the walk
terminates at A without a copy op. Copy-op chains are handled by the
recursive step. The legacy `source_layer` model is a degenerate case
(single event edge, no copy ops) of the same walk.

## 6. Copy-op Preservation

**Background.** Some dependent events need to read a source that
finished rendering earlier, possibly long enough ago that its buffer
would otherwise be reused.

**Today.** Preservation for stateful sources is bespoke engine code
(currently only shift). The compiler has no record of what is preserved
for how long.

**Limitations.**

- Shift-specific; any other stateful animation with a source dependency
  would need its own engine plumbing.
- Compiler can't reason about lifetimes of preserved content.
- Coordinate bridging between source views and event mappings makes
  reuse and lifetime analysis hard to generalize.

**Proposal.** Explicit `CopyOp` records emitted by the compiler (record
shape in `copy_ops.h`):

- Engine runs due copy ops before visual rendering for the frame with a
  monotonic cursor. Copy ops are not visual events and not layers —
  they are a preservation timeline.
- Sorted by `at`. Same-`at` ops are topologically ordered by data
  dependency; two same-`at` ops writing the same destination view are a
  compiler error (see `blob_format.md` copy-ops section). Cycles are a
  compiler error.

**Scheduling rules.**

- Each copy op is scheduled at a time when the source view actually
  contains the intended rendered pixels. Common preservation points are
  source-end or immediately before a scratch reuse.
- `source.end_sec <= dependent.at_sec`. Same-start or overlapping
  source/dependent pairs are compiler errors. v3 events carry only view
  indices, not source-event identity, so the decoder cannot check
  this — it is compiler-only.

**How it addresses the gaps.** Preservation becomes first-class compiler
data. Lifetime analysis spans the whole graph, not just shift. The
engine no longer knows about "source layers" as a concept — it runs a
generic timeline of data-movement ops.

## 7. Buffer and View Assignment

**Background.** The compiler decides buffer lifetimes for every HSVA
consumer, emits the pool size list, the PixelViewSpecs, and the
per-event view indices.

**Today.** Two storage worlds: engine-owned per-layer buffers, and a
compiler-packed shift work pool (`_pack_buffers()` bin-packs
`STATEFUL_TYPES` by non-overlapping lifetimes).

**Limitations.**

- The two worlds are separate; storage can't be shared across the
  split, even when lifetimes don't overlap.
- Reuse covers only `STATEFUL_TYPES` (shift today); everything else is
  implicitly per-layer or per-event.
- No `PixelView` concept — physical/storage mapping is encoded via
  `index_map` per layer and `remap` per event.
- No copy-op awareness.
- No partial-region reuse.

**Proposal.** Unify all HSVA storage under `PixelBufferPool`; describe
logical access via `PixelView`.

- Compiler emits the ordered real-buffer size list →
  `PixelBufferPool`.
- Compiler emits `PixelViewSpec[]` records → runtime `PixelViews`.
  Each spec carries the backing buffer index and the optional storage
  and physical index arrays.
- Events carry `src_pixv_idx`, `dst_pixv_idx`, `work_pixv_idx`.
- Copy ops carry `src_pixv_idx`, `dst_pixv_idx`.

**Reuse rules.**

- Same-layer event reuse is valid because layer events don't overlap.
- Work-buffer reuse is allowed when lifetimes don't overlap.
- Copy-op destinations and preserved source buffers participate in the
  same lifetime / safe-interval analysis.
- Copy-op destinations must not alias storage that will be overwritten
  before all dependents initialize.
- Partial reuse is allowed only when overlapping regions are proven
  disjoint.

**How it addresses the gaps.** All HSVA storage is pool-backed; every
use goes through a view. The engine has no remaining coordinate bridge.
Lifetime analysis now spans all view categories — `dst`, `src`, `work`,
and copy-op destinations — rather than only the shift work pool.

## 8. Compiler Output Changes

Wire-contract diff. Byte layout lives in `blob_format.md`.

| v2                                                   | v3                                                              |
|------------------------------------------------------|-----------------------------------------------------------------|
| `BLOB_VERSION = 2`                                   | `kBlobVersion = 3`                                              |
| Per-layer `index_map` (physical LED positions)       | Physical mapping on `PixelViewSpec`                             |
| Per-event `remap` / `remap_is_identity` / `remap_length` | Storage mapping on `PixelViewSpec`                          |
| Per-event `source_layer` (uint8, `0xFF` = none)      | Per-event `src_pixv_idx`, plus `CopyOp` records for movement    |
| Shift-only `buffer_id`                               | Per-event `work_pixv_idx` for any stateful animation            |
| (none)                                               | `PixelViewSpec[]` records                                       |
| (none)                                               | `CopyOp[]` records                                              |
| (none)                                               | `Program::target_fps` field                                     |
| (none)                                               | `Program::requires_sync` flag                                   |
| `temp_buffer` size + `max_remap_length`              | Deleted — `PixelBufferPool` owns all HSVA storage               |
| `AnimParams` tagged union                            | Deleted — each animation owns its param shape                   |
| `CompiledManifest.safe_intervals`                    | Unchanged (still controller-side; now filtered by width)        |

## 9. Naming Exception

Compiler-side Python may keep `_sec` suffixes on program-relative times
(`start_sec`, `required_start_sec`, `end_sec`). The compiler also
handles beat-space values, and the suffix disambiguates at call sites.
The runtime vocabulary in `data_model.md#time-naming-policy` still
applies to everything the compiler emits into the blob or the wire
format.

## 10. Open Items

- **Preservation policy.** Choosing between stable-dst preservation and
  an explicit copy op is the compiler's call. First-pass default:
  **prefer explicit copy ops when in doubt.** Extra HSVA usage is a
  cheaper price than compact reuse that is hard to prove correct.
  Global lifetime analysis can tighten this later once the unified pool
  + view-provenance walk have settled.
- **Controller-visible metadata surface.** Whether `target_fps` and
  `requires_sync` reach the controller via `CompiledManifest` fields or
  via blob-header parsing. TBD.
- **Width-filter placement.** Whether the `1 / target_fps` width filter
  runs per-strip, after global intersection, or after any scene-level
  merge. TBD.
- **Scene-level `target_fps` merge rule.** How a scene sequencing
  programs with different `target_fps` values resolves a single
  effective cadence. TBD with the scene/playlist surface.
- **Scene-level `requires_sync` mixing.** Whether a scene may sequence
  programs with differing `requires_sync` values, and how the runtime
  handles a switch. TBD with the scene/playlist surface.
