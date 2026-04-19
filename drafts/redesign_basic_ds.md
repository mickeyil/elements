# Redesign: Core Animation Data Structures

## Goal

Simplify the runtime's core data structures while preserving the current
compiler/runtime split and the current playback semantics.

The main targets are:

- remove the engine's current two-stage pixel routing (`remap` + `index_map`)
- remove the runtime animation factory from the engine
- keep stateful animations such as shift correct
- keep memory allocation predictable on ESP32
- make the data model easier to reason about in both the compiler and decoder

This document is intentionally higher level than the code sketch. The concrete
draft API lives in separate files under `drafts/`:

- `colors.h`
- `gamma.h`
- `gamma.cpp`
- `runtime_constants.h`
- `hardware_profile.h`
- `animation_types.h`
- `blob_limits.h`
- `blob_reader.h`
- `blob_reader.cpp`
- `copy_ops.h`
- `copy_ops.cpp`
- `pixel_buffer_pool.h`
- `pixel_buffer_pool.cpp`
- `pixel_view.h`
- `pixel_view.cpp`
- `pixel_views.h`
- `pixel_views.cpp`
- `animation.h`
- `layer.h`
- `layer.cpp`
- `strip.h`
- `strip.cpp`
- `compositor.h`
- `compositor.cpp`
- `engine.h`
- `engine.cpp`
- `synced_clock.h`
- `synced_clock.cpp`
- `playback.h`
- `playback.cpp`
- `program_structs.h`
- `program_structs.cpp`
- `decoder.h`
- `decoder.cpp`

Three prose files complement the draft sources:

- `blob_format.md` — exact byte contract for blob format v3
- `decoder.md` — decoder background, integration notes, and per-animation
  factory contract
- `synced_clock.md` — sync policy, wire format, and controller-side
  integration for `SyncedClock`

## Current Problems

The current structures in `src/decoder.h` / `src/engine.cpp` have two main
pain points.

### 1. Pixel routing is split across two levels

Today an event may render through:

- an event-local `remap` array
- a layer-local `index_map`

That means the engine has to bridge two coordinate systems at runtime. The
most awkward case is shift initialization, where the engine joins source and
dependent layers through physical LED indices.

### 2. Animation construction happens in the engine

The blob currently decodes into inert `AnimParams`, and the engine later turns
those into concrete animation instances via `create_animation()`. That couples
the engine to every animation type and mixes "decoded artifact" concerns with
"runtime activation" concerns.

### 3. HSVA memory is fragmented across multiple allocation sites

Today the decoder allocates the shift work-buffer pool, while the engine later
allocates layer buffers separately. That works, but it spreads the main HSVA
memory consumers across multiple allocations.

## New Design Summary

The updated direction has three core pieces:

### `PixelBufferPool`

`PixelBufferPool` owns the real `hsva_t` backing buffers used by the decoded
program.

- The compiler emits an ordered list of real buffer sizes.
- The decoder allocates those buffers through one pool abstraction.
- The pool may internally use one contiguous allocation and carve it into
  slices, but that is an implementation detail of the pool.

The important design point is that the pool owns real storage, while the rest
of the runtime mostly talks in views.

The current draft code keeps the `PixelBufferPool` API intentionally small:

- initialize from an ordered list of buffer sizes
- resolve a backing buffer by index
- query a backing buffer's size
- release owned storage

It does not currently expose extra clear/reset helpers beyond full teardown.

### Strip-size limit and integer widths

The draft direction assumes a maximum strip length of 1000 LEDs.

Pixel positions, strip lengths, physical LED indices, PixelView sizes, and
per-buffer HSVA sizes therefore use `uint16_t`.

`ColorOrder`, `DeviceState`, and small bounded counts such as layer count can
remain `uint8_t`.

### Time Naming Policy

The redesign should not introduce new `t_rel` or `t0` names.

Use this naming rule:

- loose values, parameters, locals, and return values use strict domain names
- `t_<domain>` means float seconds relative to that domain
- `<thing>_us` / `<thing>_ns` means an absolute timestamp or delta with explicit
  units
- delta names must encode direction/sign at public API boundaries unless the
  sign convention is pinned nearby (e.g. in the function's own doc comment or
  by the enclosing type)
- struct fields may use short contextual names only when the struct type pins
  both meaning and units
- compiler fields may keep `_sec` because compiler code also handles beat-space
  values

Final runtime vocabulary:

| Name | Meaning |
| --- | --- |
| `t_program` | float seconds relative to program start |
| `t_animation` | float seconds relative to animation/event start |
| `program_start_us` | absolute program start timestamp in selected clock domain |
| `program_clock_now_us()` | absolute now in the selected program clock domain |
| `now_remote_us()` | absolute remote-domain timestamp |
| `now_local_us()` | absolute device-local monotonic timestamp |
| `SyncedClock` | synced clock abstraction exposing remote/local domains |
| `is_synced()` | status predicate for remote-domain validity |
| `apply_sync_offset(offset_us, valid_for_us)` | applies a sync-offset lease; `offset_us` = local minus remote |
| `render_next_frame()` | accepts the next renderable frame from the selected clock/state |
| `RenderFrameResult` | presentation-side result from a playback call that may update `_strip` |
| `current_t_program()` | current program-time cursor |
| `_t_program_cursor_us` | minimum accepted program position in integer microseconds |
| `target_fps` | program-level intended presentation cadence in Hz |

Contextual struct fields:

| Field | Meaning |
| --- | --- |
| `AnimationEvent::start` | program-relative float seconds |
| `AnimationEvent::duration` | float seconds |
| `CopyOp::at` | program-relative float seconds |
| `Program::duration` | float seconds |
| `Program::target_fps` | intended presentation cadence in Hz |

Frame/reporting/protocol names should also use the same vocabulary:

- `DeviceFrame::t_program`
- `ProgramFrame::t_program`
- UDP frame header field `t_program`
- start/resume command field `program_start_us`
- jump target field `t_program`

These frame/reporting/protocol names are the intended contract for the follow-up
implementation rename pass. The live tree may still contain legacy `t_rel` /
`t0_us` names until that pass lands.

Names to avoid in new runtime code:

- `t_rel`
- `t0`
- `synced time` / `unsynced time` as clock-domain names

### `PixelView`

`PixelView` is a small runtime class that wraps logical pixel access into a
real backing buffer and, for destination views, records where those pixels
appear on the physical strip.

It contains:

- a pointer to the backing `hsva_t` buffer
- an optional owned storage index indirection array
- an optional owned physical output index array
- a logical length

Its main APIs answer two different questions:

- `view[i]`
  - memory access for animations and copy ops
  - uses the optional storage mapping
- `view.physical_index(i)`
  - output routing for the compositor
  - uses the optional physical mapping

Storage mapping and physical mapping are intentionally independent:

- storage mapping describes where logical pixels live in HSVA memory
- physical mapping describes where logical pixels appear on the LED strip

This keeps the animation-facing code simple. Animations see a logical pixel
space; `PixelView` hides whether that space is direct or reindexed.

The dominant case is identity storage. Storage indirection remains available
because it is the cleanest way to build source views over preserved buffers
when a later consumer needs a subset or a different logical order.

Physical mapping is required only for destination views that may be passed to
the compositor. Source, work, and copy-internal views can be storage-only.

`PixelView` does **not** own its backing `hsva_t` buffer. That buffer still
belongs to `PixelBufferPool`.

It **does** own its optional storage/physical index metadata. This keeps the
runtime view object self-contained and avoids leaking decode-time descriptor
lifetime into the runtime ownership model.

### `PixelViews`

`PixelViews` owns the runtime `PixelView[]` table.

It is responsible for:

- allocating the runtime `PixelView[]` array
- initializing each runtime `PixelView` from decoder input metadata
- disposing the runtime view array on teardown

This keeps `Program` simpler:

- `PixelBufferPool` owns real pixel buffers
- `PixelViews` owns runtime logical views

The current draft uses temporary decoder input metadata named `PixelViewSpec`.
`PixelViewSpec` is construction input only; it is not retained in `Program`.

### Three-view event model

Each event references up to three `PixelView`s:

- `src_pixv_idx`
  - optional
  - read-only input used during `initialize()`
- `dst_pixv_idx`
  - required
  - render target for `render()`
  - must have physical mapping because it may be composited
- `work_pixv_idx`
  - optional
  - persistent mutable storage used by stateful animations

This is the key semantic split:

- `src` is initialization input
- `dst` is current-frame output
- `work` is event-local storage that remains valid for the lifetime of the
  active event

That model keeps shift correct without reintroducing today's coordinate bridge.

### `CopyOps`

`CopyOps` is an internal source-preservation timeline.

Each `CopyOp` has:

- `at`
  - program-relative time when the copy becomes due
- `src_pixv_idx`
  - source logical view
- `dst_pixv_idx`
  - destination logical view

Copy ops are not visual events and are not layers. The engine runs due copy ops
before visual rendering for the current frame.

The intended use is explicit preservation:

- an earlier visual event renders output into some buffer/view
- a compiler-scheduled copy op copies the relevant logical source pixels into a
  stable buffer/view
- a later dependent animation initializes from that stable source view

This keeps the runtime uniform: preservation is represented as data movement
between PixelViews, not as hidden source-layer logic inside the engine.

Important semantic rule:

- `source=` must resolve to a source event whose `source.end_sec <=
  dependent.at_sec`
- same-start or overlapping source/dependent pairs are compiler errors
- copy ops preserve already-rendered PixelView contents; they do not evaluate
  an animation mathematically

Important timing recommendation:

- the compiler must schedule a copy op at a time where the source view already
  contains the intended rendered pixels
- common preservation points are source end or immediately before scratch
  storage reuse

### Jump correctness via safe intervals

Source preservation only works when the source events that feed a copy op
have actually been rendered. A naive jump into the middle of a program
would let the engine reset its buffers, advance the copy-op cursor past
unfired source events, and then copy zeros into a downstream view — silent
corruption at the next consumer.

The redesign relies on the existing **safe-interval mechanism** to make
this unreachable, not on any new runtime check:

1. The compiler computes, for every event with a source dependency, a
   `required_start_sec` that extends the event's unsafe span back through
   the entire view-provenance chain to the originating source event(s).
   For an event E that reads view B, where B was last written by event A
   (directly, or via a copy op chain rooted at A), E's
   `required_start_sec` is A's required start — which is `A.start` if A
   has no source dependencies of its own, or the recursive minimum if A
   itself reads a preserved view.
2. The compiler emits `safe_intervals` as the complement of the union of
   all unsafe spans. Before publishing those intervals, it drops
   nonzero candidate safe intervals narrower than one target frame period
   (`1 / Program::target_fps`). The `t_program == 0` start sentinel is
   preserved separately from the nonzero interval list, so startup safety does
   not weaken the width/headroom rules for jump targets.
3. The controller refuses to send any command that **reconstructs engine
   state at a nonzero `t_program`** (`JUMP`, live rejoin / re-LOAD at
   nonzero time, `START` at nonzero time) when the target time is not inside
   a safe interval. `START` at `t_program == 0` is always safe by construction.
4. The runtime trusts the controller. `Engine::reset()` clears buffers and
   `run_copy_ops_until()` advances the copy cursor monotonically — both
   are correct *given* that the time target is in a safe interval, because
   any garbage write a fired-but-source-unrendered copy op produces is
   guaranteed to be either (a) overwritten by a later writer that *does*
   have a rendered source, or (b) never read by any consumer (any consumer
   that would read it has its own `required_start_sec` that excludes the
   current target).

**Ordinary `RESUME` from `PAUSE` is not gated by safe intervals.**
`Playback::handle_resume` does not call `Engine::reset()`; the copy cursor,
layer cursors, and HSVA buffers are intact, so resuming forward from the
paused position is correct regardless of whether that position is inside a
safe interval. The contract relies on the controller using `RESUME` only
for resuming-where-paused; meaningful retiming goes through `JUMP`, which
is gated.

Implications:

- Programs with elaborate source chains have shorter safe intervals. That
  is the intended trade-off, not a bug.
- The controller does not need to validate safe-interval widths itself. The
  compiler only publishes nonzero safe intervals wide enough for the program's
  declared presentation cadence. The controller still must choose a target with
  one target-frame period of headroom when it joins into the middle of a safe
  interval.
- Live rejoin targets a safe reconstruction point: the current live time if it
  is inside a safe interval with at least one target-frame period remaining,
  otherwise the start of the next future safe interval. The controller sends
  `LOAD`, then `JUMP(safe_point_t)`, then `RESUME(program_start_us)` if the
  session is currently playing. The device waits until the clock reaches the
  cursor before rendering; that wait is effectively zero when the target is the
  current live time.
- Paused rejoin uses the same safe-point rule but skips `RESUME` until the
  session actually resumes. If the paused position is unsafe or lacks
  target-frame headroom, the controller may jump to a later safe point; the
  device waits there.
- If there is no future safe interval in the current program segment, the
  controller cannot safely rejoin that device into the segment.
- The runtime needs no new state, no warmup pass, no keyframes, and no
  defensive assertions on jump targets. The compiler proves safety; the
  controller enforces it; the runtime executes.
- Offline render walks frames sequentially from `0`, so safe intervals are
  irrelevant to it.
- This is the compiler-proves / controller-gates / runtime-trusts pipeline.
  Adding a runtime safe-interval check would require shipping
  `safe_intervals` in the blob (currently they live in `CompiledManifest`
  on the controller side); not currently planned.

Sampling caveat:

- `source=` captures preserved rendered output, not mathematical animation
  output
- if playback never samples the source interval, there is no rendered
  output to preserve — but the safe-interval analysis above prevents any
  consumer from reaching such a state legitimately
- this still matters for very short events at coarse offline render
  steps, where a frame sample could fall outside a short event entirely
- `target_fps` makes that sampling contract explicit at program scope. Authors
  can override the default cadence when needed, but the compiler and offline
  render use one cadence for the whole program, not per-animation rates.

### Compiler responsibilities for copy-op safety

The compiler must:

- emit copy ops as v3 blob records (see `blob_format.md`)
- emit `Program::target_fps`, defaulting to 50 Hz unless the program overrides
  it
- compute `required_start_sec` for every event with a source dependency
  by walking a **view-provenance graph** (see below)
- emit safe intervals computed from those extended unsafe spans
- filter nonzero safe intervals narrower than one target frame period while
  preserving the `t_program == 0` start sentinel
- enforce the per-event sortedness and non-overlap rules within layers
- enforce the copy-op sortedness rule
- topologically order same-time copy ops by data dependency, and reject
  cycles or two same-time copy ops that write to the same destination
  view (see "equal `at` values" in `blob_format.md`'s copy-ops section)
- validate the legacy v2 invariant that each source dependency resolves
  only to a writer whose end is `≤` the dependent's start (the decoder
  cannot see source-event identity in v3, so this check is compiler-only)

#### View-provenance graph

The redesign supports two preservation forms (see Open Items below):

1. **Stable dst preservation.** Event A writes view V; nothing else writes
   V before event B reads V. No copy op is involved — V is preserved
   simply because the compiler picked a buffer assignment that keeps it
   live.
2. **Copy-op preservation.** Event A writes view V'; before V' is
   overwritten, a copy op fires at time `t_c` copying V' into view V;
   event B later reads V.

The provenance walk handles both forms uniformly. The graph has two edge
types:

- **Event → view**: an `AnimationEvent` writes its `dst_pixv_idx` view
  during `[event.start, event.start + event.duration)`. The "writer" of
  that view at any time inside the event's interval (and forward until
  the next writer) is that event.
- **CopyOp → view**: a copy op writes its `dst_pixv_idx` view at time
  `at`. The write transitively depends on the writer of the copy op's
  `src_pixv_idx` at time `at`.

Then `required_start_sec` for an event E with `src_pixv_idx = V` is
computed by:

1. Find the most recent writer to V at any time `≤ E.start`. The writer
   may be an event's `dst_pixv_idx` or a copy op's `dst_pixv_idx`.
2. If the writer is a copy op, recursively walk to the writer of the copy
   op's `src_pixv_idx` (using the same rule).
3. If the writer is an event A, the chain terminates at A. Take A's
   `required_start_sec` (or `A.start` if A has no src dependencies).
4. Set E's `required_start_sec` to the earliest start in the chain.

The same walk applies to `work_pixv_idx` if the work view is initialized
from preserved content (and not just used as scratch).

Today's `_compute_required_starts` walks only the legacy `source_layer`
field on each event. The v3 update replaces it with the generic
view-provenance walk above, which subsumes both the legacy
implicit-source-layer model and the new explicit copy-op model.

## Animation Interface

The animation interface changes from "render into a flat buffer" to "render
into a logical pixel view."

The intended shape is:

- `initialize(const PixelView* src, PixelView* work)`
- `render(PixelView& dst, float t_animation)`

Notes:

- `dst` is always present
- `src` and `work` are optional
- simple stateless animations such as wave/spark/paint can ignore both
  optional views
- shift uses `src` and `work`

The decoder constructs animation objects directly at decode time. The engine no
longer needs a runtime factory switch on animation type.

## Why `work` Exists

Stateful animations may need mutable storage that is distinct from the current
render target.

Shift is the motivating example:

- on first activation, it snapshots source pixels into `work`
- on each frame, it reads from `work` and writes into `dst`

`work` must not alias mutable output storage for animations that require a
stable snapshot. The compiler is responsible for assigning valid work storage
for the duration of each event.

## Views, Layers, And Compositor

The updated direction removes the canonical layer buffer concept.

All HSVA storage is just pool storage. PixelViews describe how visual events,
copy ops, source initialization, work storage, and compositor output access
that storage.

### `Layer`

`Layer` is a timeline of visual events.

It owns:

- the decoded `AnimationEvent[]`

It does not own or borrow:

- HSVA buffers
- physical maps
- layer buffer lengths
- buffer indices

This is the main simplification from the latest discussion. A layer answers:

- which event is active at time `t`?

It no longer answers:

- where is this layer's compositing buffer?
- how does this layer map buffer positions to physical LEDs?

Those answers moved to `PixelView`.

### `Compositor`

The compositor receives active destination views in layer order:

```cpp
void composite(Strip& out, PixelView* const* active_dst_views, uint8_t count);
```

Each entry is:

- `nullptr`
  - layer has no active visual event for this frame
- non-null `PixelView*`
  - active destination view rendered by that layer for this frame

The compositor never includes `layer.h`, never sees event cursors, and never
knows about layer buffers. It only needs:

- `view[i]`
  - the HSVA pixel to blend
- `view.physical_index(i)`
  - the physical LED to write

This keeps the hot path direct while removing the old two-level routing model.

### Scratch, Stable, And Work Storage

Scratch/stable/work buffers still exist, but they are just entries in
`PixelBufferPool`.

The compiler decides:

- which real pool buffers exist
- which PixelViews point at those buffers
- which events can reuse the same backing storage
- which outputs must be preserved in stable storage for later dependencies
- when copy ops are required to move data into preserved/work views

The runtime does not need buffer "types". It sees only:

- pool buffer sizes
- PixelView descriptors
- visual event view indices
- copy-op view indices

## Engine / Compositor / Strip Boundary

The current direction is:

- `Engine` owns `Compositor` internally
- callers render frames via `Engine::render_frame(t_program, Strip&)`
- callers do not need to know compositor details

`Engine` owns visual layer progression and the copy-op cursor. The intended
playback state per layer is:

- `cursor`
  - current event index
- `initialized`
  - whether `initialize()` has already run for the active event

The old `instance == nullptr` activation signal disappears because animation
objects are now decoder-constructed and stored directly on events.

Engine also owns:

- a monotonic copy-op cursor
- a reused `active_dst_views[]` array, one entry per layer

The high-level frame order is:

1. run due copy ops with `op.at <= t_program`
2. clear `active_dst_views[]` to `nullptr`
3. walk layer timelines and find active visual events
4. initialize newly active events with optional `src` / `work`
5. render each active event into its `dst` PixelView
6. store each active `dst` view in `active_dst_views[layer_index]`
7. ask `Compositor` to blend active views into `Strip`

This replaces the old `active_mask`. Layer activity is represented directly by
whether the active-view pointer is null.

Copy ops are sorted by time, so the engine can process them with a simple
monotonic cursor.

Animation render contract:

- `render(dst, t_animation)` must fully define every logical pixel in `dst` on
  every frame
- engine-side activation clear is allowed as defensive hygiene, but should not
  be required for correctness

### `Strip`

`Strip` is the canonical final RGB frame buffer.

It is intended to be more than today's thin byte wrapper:

- owned exact-sized RGB storage allocated from `HardwareProfile::strip_length`
- array-like RGB pixel access
- size / clear helpers
- raw byte access when needed
- final `copy_to(..., ColorOrder)` helper for hardware-facing buffers

The important semantic rule is:

- rendering produces canonical linear RGB in `Strip`
- simulator/tests/stdout may inspect that RGB directly
- hardware-facing transforms happen afterward

### Output transforms

The agreed current direction is:

- gamma correction is represented by a caller-owned `GammaCorrection`
  lookup table
- `GammaCorrection` defaults to identity gamma, so sim/tests/debug output can
  use the same output path without a separate gamma-enabled flag
- firmware/output owners call `set_gamma()` once from their selected hardware
  profile or output policy
- invalid gamma values leave the previous LUT unchanged
- gamma correction is an external in-place transform:
  - `apply_gamma(Strip&, const GammaCorrection&)`
- channel reordering is a last-mile copy:
  - `Strip::copy_to(dst, ColorOrder)`

These transforms sit outside `Engine` and `Compositor`.

`ColorOrder` is intentionally limited to `RGB` and `BGR` in the current draft.
Additional channel layouts should be added only when a real strip requires
them.

The current draft accepts:

- `1.0`
  - identity gamma
- `> 1.0` and `<= kMaxSupportedGamma`
  - generated LED correction LUT

The default WS2812 dark-room starting point is `kWs2812DarkRoomGamma`, currently
`2.8`. This matches the fixed gamma table in today's `src/colors.cpp`.

## Compiler / Decoder Responsibilities

### Compiler

The compiler owns:

- layer inference and layer ordering
- allocation/reuse of real HSVA buffers
- generation of the ordered real-buffer size list
- generation of `PixelViewSpec` records
- assigning `src_pixv_idx`, `dst_pixv_idx`, and `work_pixv_idx` per event
- generation of `CopyOp` records for explicit preservation copies
- validating that work-storage reuse is safe
- validating that source-preservation storage remains valid until all
  dependent events have initialized
- validating that physical mappings on dst views stay within strip length

Important reuse rules:

- same-layer event reuse remains valid because layer events do not overlap
- work-buffer reuse is allowed when lifetimes do not overlap
- copy-op destinations and preserved source buffers participate in the same
  lifetime/safe-interval analysis
- copy-op destinations must not alias storage that will be overwritten before
  all dependents initialize
- source dependencies must refer to already-ended source events
- partial reuse is allowed only when overlapping regions are proven disjoint

Recommendation:

- keep the decoder/runtime dumb
- let the compiler perform global lifetime analysis
- represent preservation with explicit copy ops when that is easier to reason
  about than complex compile-time source rewrites

### Decoder

The decoder owns:

- allocating `PixelBufferPool`
- building the runtime `PixelViews` table from the compiler's `PixelViewSpec`s
- building the runtime `CopyOps` table
- constructing concrete animation objects from decoded params
- wiring layers, views, copy ops, and events together into `Program`

The decoder should not need animation-type-specific memory policy. It should
mostly consume indices, sizes, and descriptors emitted by the compiler.

Decoder validation should include:

- PixelView buffer indices are valid
- storage indices are within the referenced pool buffer
- physical indices are within `HardwareProfile::strip_length`
- event `dst_pixv_idx` is present and references a compositable PixelView
- event `src_pixv_idx` and `work_pixv_idx` are absent or valid
- copy ops are sorted by `at`
- copy-op source/destination view indices are valid
- copy-op source and destination sizes match

The decoder enforces only structural and runtime-visible constraints. v3
events do not carry source-event identity (only view indices), so the
decoder cannot verify "source dependencies resolve only to events whose
end ≤ dependent.start". That semantic check is a compiler responsibility
(see "Compiler responsibilities for copy-op safety" earlier in this doc).

## Memory Model

The design objective is to keep HSVA memory allocation compact and predictable.

The intended direction is:

- the compiler emits an ordered list of real buffer sizes
- the decoder allocates the pool once
- each pool buffer is resolved by index
- runtime `PixelView`s are built from temporary `PixelViewSpec`s
- each runtime `PixelView` points into one resolved real buffer
- layer timelines reference PixelViews by index
- copy ops reference PixelViews by index

This avoids scattering the main HSVA allocations across unrelated code paths.

The design does **not** require raw byte offsets in the blob. Ordered buffer
sizes plus stable indices are sufficient.

### Build-mode allocation strategy

The current draft direction is:

- `ARDUINO` build:
  - `PixelBufferPool` uses one compact pooled allocation
  - this is the firmware-oriented implementation and is aimed at minimizing
    fragmentation on ESP32
- non-`ARDUINO` build:
  - `PixelBufferPool` uses one allocation per logical buffer
  - this keeps host-side tools such as Valgrind effective at catching
    inter-buffer out-of-bounds writes

This split is an implementation detail of `PixelBufferPool`. The public pool
API stays the same in both cases.

### Alignment and layout assumptions

No extra runtime alignment logic is required in the current design because:

- the pool stores only `hsva_t`
- slices are taken only on `hsva_t` boundaries, not byte boundaries

Even so, the design should explicitly assert the assumptions it relies on.
See the draft `colors.h` note alongside this document.

## Engine Lifecycle

The engine still owns activation state.

Per active layer/event, the engine must know at least:

- current event cursor
- whether the current event has already been initialized
- copy-op cursor

The engine is responsible for ensuring:

- due copy ops run before visual rendering for the frame
- `initialize()` runs before the first `render()` of an activation
- after reset / jump / restart, the event is treated as uninitialized again
- buffers that must start cleared are cleared by engine/program reset
- layer activity for compositing is derived directly from event timing in the
  render loop, not from any old animation-instance pointer
- active compositor input is the active dst view array, not an active bitmask

The current leaning is to keep this lifecycle state in the engine, not inside
the animation base class.

## What This Replaces

The intended simplifications are:

- `AnimParams` no longer remain inert until activation; decoder constructs
  `Animation` objects directly
- engine-side `create_animation()` goes away
- event-side `remap` / `remap_is_identity` / `remap_length` go away
- layer-side `index_map` / `physical_map` compositing metadata goes away
- `temp_buffer` goes away
- `active_mask` goes away
- the current shift coordinate bridge goes away
- today's shift work-buffer pool becomes a more general `PixelBufferPool` +
  `work_pixv_idx` model

## Things To Keep Explicit

These points should stay explicit in the design:

- `dst` is mandatory; `src` and `work` are optional
- `dst` views must have physical mapping; `src` and `work` views do not
- `work` is persistent event-local pixel storage, not just a scratch pointer
- `PixelView` is non-owning for backing pixel buffers but owning for optional
  storage/physical index metadata
- storage mapping and physical mapping are separate concepts
- `Layer` is a timeline, not a compositing surface
- `CopyOp` is an internal preservation operation, not a visual event and not a
  layer
- the compiler owns buffer reuse correctness
- the compositor works from active dst views in layer order

## Open Items

These areas are intentionally not locked in yet:

- whether pool internals are represented as one contiguous block, a small set
  of chunks, or another pool implementation detail
- whether `PixelView` should expose any helpers beyond `operator[]`,
  `physical_index()`, `size()`, and `clear()`
- exact compiler policy for when to preserve by stable dst buffer vs. by
  explicit copy op
- future animation types that may need additional runtime state beyond pixel
  storage

The exact blob layout for `PixelBufferPool`, `PixelView` descriptors, and
`CopyOp` records is now defined in `blob_format.md`; the parser shape lives
in `decoder.h` / `decoder.cpp`; integration notes are in `decoder.md`.

## Affected Code

The main areas affected by this redesign are:

- `drafts/colors.h`
- `drafts/gamma.h`
- `drafts/gamma.cpp`
- `drafts/runtime_constants.h`
- `drafts/animation_types.h`
- `drafts/blob_limits.h`
- `drafts/blob_reader.h`
- `drafts/blob_reader.cpp`
- `drafts/copy_ops.h`
- `drafts/copy_ops.cpp`
- `drafts/pixel_buffer_pool.h`
- `drafts/pixel_buffer_pool.cpp`
- `drafts/pixel_view.h`
- `drafts/pixel_view.cpp`
- `drafts/pixel_views.h`
- `drafts/pixel_views.cpp`
- `drafts/animation.h`
- `drafts/layer.h`
- `drafts/layer.cpp`
- `drafts/strip.h`
- `drafts/strip.cpp`
- `drafts/compositor.h`
- `drafts/compositor.cpp`
- `drafts/engine.h`
- `drafts/engine.cpp`
- `drafts/synced_clock.h`
- `drafts/synced_clock.cpp`
- `drafts/synced_clock.md`
- `drafts/playback.h`
- `drafts/playback.cpp`
- `drafts/program_structs.h`
- `drafts/program_structs.cpp`
- `drafts/decoder.h`
- `drafts/decoder.cpp`
- `drafts/blob_format.md`
- `drafts/decoder.md`
- `src/decoder.h` / `src/decoder.cpp` (replaced by drafts/decoder.{h,cpp})
- `src/animation.h`
- concrete animation headers, especially `anim_shift.h`
- `src/engine.h` / `src/engine.cpp`
- `src/compositor.h` / `src/compositor.cpp`
- `src/colors.h` / `src/colors.cpp`
- new `src/gamma.h` / `src/gamma.cpp`
- `compiler/elements/compiler.py` (incl. update to `_compute_required_starts` to walk copy-op chains)
- `compiler/elements/blob.py` (replaced by v3 emitter matching `blob_format.md`)

The draft source files are meant to make those changes easier to refine
without having to reconstruct the data model from discussion snippets.
