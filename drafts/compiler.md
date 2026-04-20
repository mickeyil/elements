# Compiler Safety Contract

What the compiler must prove so the runtime can stay simple. The runtime
data model, time-naming vocabulary, and component shapes live in
`data_model.md`.

## Trust Boundary

The pipeline is **compiler-proves / controller-gates / runtime-trusts**:

- the compiler computes safety metadata (safe intervals, provenance,
  lifetimes) from the global program
- the controller refuses to issue commands that would reconstruct engine
  state outside those safe windows
- the runtime executes without defensive checks on jump targets

Adding a runtime safe-interval check would require shipping
`safe_intervals` in the blob (they currently live in `CompiledManifest` on
the controller side). Not planned.

## Safe Intervals

Source preservation only works when the events that feed a copy op have
actually been rendered. A naive jump into the middle of a program would let
the engine reset its buffers, advance the copy-op cursor past unfired source
events, and then copy zeros downstream — silent corruption at the next
consumer.

Safe intervals make that unreachable without adding runtime state:

1. For every event `E` with a source dependency, the compiler computes
   `required_start_sec` by walking the view-provenance graph back to the
   originating source event(s). See §View-Provenance Graph.
2. The compiler emits `safe_intervals` as the complement of the union of
   all unsafe spans. It drops nonzero candidate intervals narrower than
   one target frame period (`1 / Program::target_fps`). The
   `t_program == 0` start sentinel is preserved separately, so startup
   safety does not weaken the width/headroom rule for jump targets.
3. The controller refuses any command that **reconstructs engine state at a
   nonzero `t_program`** — `JUMP`, live rejoin / re-`LOAD` at nonzero
   time, `START` at nonzero time — when the target is not inside a safe
   interval. `START` at `t_program == 0` is always safe by construction.
4. The runtime trusts the controller. `Engine::reset()` clears buffers and
   `run_copy_ops_until()` advances the copy cursor monotonically. Both are
   correct *given* a safe target, because any garbage a fired-but-source-
   unrendered copy op writes is guaranteed to be either (a) overwritten
   before any consumer reads it, or (b) never read at all (every reader's
   own `required_start_sec` excludes the current target).

### RESUME is not gated

`Playback::handle_resume` does not call `Engine::reset()`. The copy cursor,
layer cursors, and HSVA buffers are intact, so resuming forward from the
paused position is correct regardless of whether that position is inside a
safe interval. The contract relies on the controller using `RESUME` only
for resuming-where-paused; meaningful retiming goes through `JUMP`, which
is gated.

### Live rejoin

Rejoin targets a safe reconstruction point: the current live time if it is
inside a safe interval with at least one target-frame period of headroom,
otherwise the start of the next future safe interval.

- **playing session**: controller sends `LOAD`, `JUMP(safe_point_t)`, then
  `RESUME(program_start_us)`. The device waits until the clock reaches the
  cursor before rendering — effectively zero when the target is the current
  live time.
- **paused session**: same safe-point rule, without the final `RESUME`. The
  device waits at the safe point.
- If there is no future safe interval in the current program segment, the
  controller cannot safely rejoin the device into that segment.

### Implications

- Programs with elaborate source chains have shorter safe intervals. That
  is the intended trade-off, not a bug.
- The controller does not validate safe-interval widths itself; the
  compiler has already filtered them. The controller still must pick a
  target with one target-frame period of headroom when joining into the
  middle of an interval.
- Offline render walks frames sequentially from `0`, so safe intervals are
  irrelevant to it.

### Sampling caveat

`source=` captures preserved *rendered* output, not mathematical animation
output. If playback never samples the source interval there is no rendered
output to preserve — but the safe-interval analysis above prevents any
consumer from reaching such a state legitimately.

This still matters for very short events at coarse offline-render steps,
where a frame sample can fall outside a short event entirely. `target_fps`
makes the sampling contract explicit at program scope. Authors can override
the default cadence, but compiler and offline render use one cadence for
the whole program, not per-animation rates.

## View-Provenance Graph

The redesign supports two preservation forms:

1. **Stable dst preservation.** Event A writes view V; nothing else writes
   V before event B reads V. No copy op is needed — V is preserved because
   the compiler picked a buffer assignment that keeps it live.
2. **Copy-op preservation.** Event A writes view V'; before V' is
   overwritten, a copy op fires at `t_c` copying V' into V; event B later
   reads V.

The provenance walk handles both uniformly. Two edge types:

- **Event → view**: an `AnimationEvent` writes its `dst_pixv_idx` during
  `[event.start, event.start + event.duration)`. The writer of that view at
  any time inside the interval (and forward until the next writer) is that
  event.
- **CopyOp → view**: a copy op writes its `dst_pixv_idx` at time `at`. The
  write transitively depends on the writer of the copy op's `src_pixv_idx`
  at time `at`.

`required_start_sec` for an event `E` with `src_pixv_idx = V`:

1. find the most recent writer to V at any time `<= E.start`; the writer
   may be an event's `dst_pixv_idx` or a copy op's `dst_pixv_idx`
2. if the writer is a copy op, recursively walk to the writer of its
   `src_pixv_idx` under the same rule
3. if the writer is an event A, the chain terminates; take A's own
   `required_start_sec` (or `A.start` if A has no source dependencies)
4. set E's `required_start_sec` to the earliest start in the chain

The same walk applies to `work_pixv_idx` when the work view is initialized
from preserved content (not just used as scratch).

The legacy `_compute_required_starts` walked only the `source_layer` field
per event. The v3 update replaces it with the walk above, which subsumes
both the legacy implicit-source-layer model and the explicit copy-op
model.

## Compiler Responsibilities

The compiler owns:

- layer inference and layer ordering
- allocation / reuse of real HSVA buffers
- the ordered real-buffer size list for `PixelBufferPool`
- `PixelViewSpec` records for `PixelViews`
- per-event `src_pixv_idx`, `dst_pixv_idx`, `work_pixv_idx`
- `CopyOp` records for explicit preservation
- `Program::target_fps` (default 50 Hz unless overridden)
- `required_start_sec` via the view-provenance walk
- safe intervals + nonzero-interval filtering + the `t_program == 0`
  sentinel
- per-layer event sortedness and non-overlap
- copy-op sortedness by `at`
- topological ordering of same-`at` copy ops by data dependency; rejection
  of cycles and of two same-`at` copy ops that write the same destination
  view (see `blob_format.md` copy-ops section)
- the v2 source-dependency invariant: each source dependency resolves only
  to a writer whose end is `<= dependent.start`. v3 events do not carry
  source-event identity (only view indices), so the decoder cannot check
  this — it is compiler-only.

### Copy-op scheduling

The compiler must schedule each copy op at a time where the source view
actually contains the intended rendered pixels. Common preservation points
are source end or immediately before a scratch-storage reuse.

`source=` must resolve to a source event whose `source.end_sec <=
dependent.at_sec`. Same-start or overlapping source/dependent pairs are
compiler errors.

### Buffer reuse rules

- same-layer event reuse is valid because layer events do not overlap
- work-buffer reuse is allowed when lifetimes do not overlap
- copy-op destinations and preserved source buffers participate in the
  same lifetime / safe-interval analysis
- copy-op destinations must not alias storage that will be overwritten
  before all dependents initialize
- partial reuse is allowed only when overlapping regions are proven
  disjoint

## Naming Exception

Compiler-side Python may keep `_sec` suffixes on program-relative times
(`start_sec`, `required_start_sec`, `end_sec`). The compiler handles
beat-space values alongside seconds, and the suffix disambiguates at call
sites. The runtime vocabulary in `data_model.md#time-naming-policy` still
applies to everything the compiler emits into the blob or the wire format.

## Open Items

### Preservation policy

Choosing between stable dst preservation and explicit copy ops is the
compiler's call. The current default is **prefer explicit copy ops when in
doubt** — a working system at the cost of some extra HSVA usage is better
than compact reuse that is hard to prove correct. Global lifetime analysis
can tighten this later.
