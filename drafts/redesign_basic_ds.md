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
- safe-interval analysis must include both visual events and copy ops

Sampling caveat:

- `source=` captures preserved rendered output, not mathematical animation
  output
- if playback never samples the source interval, there may be no rendered
  output to preserve
- this mainly affects very short events, runtime stalls, jumps, and coarse
  offline render steps

## Animation Interface

The animation interface changes from "render into a flat buffer" to "render
into a logical pixel view."

The intended shape is:

- `initialize(const PixelView* src, PixelView* work)`
- `render(PixelView& dst, float t_rel)`

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
- callers render frames via `Engine::render_frame(t_rel, Strip&)`
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

1. run due copy ops with `op.at <= t_rel`
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

- `render(dst, t_rel)` must fully define every logical pixel in `dst` on every
  frame
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
- source dependencies resolve only to source events with `end_sec <=
  dependent.at_sec`
- copy ops are sorted by `at`
- copy-op source/destination view indices are valid
- copy-op source and destination sizes match

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

- exact blob layout for `PixelBufferPool` and `PixelView` descriptors
- exact blob layout for `CopyOp` records
- whether pool internals are represented as one contiguous block, a small set
  of chunks, or another pool implementation detail
- whether `PixelView` should expose any helpers beyond `operator[]`,
  `physical_index()`, `size()`, and `clear()`
- exact compiler policy for when to preserve by stable dst buffer vs. by
  explicit copy op
- future animation types that may need additional runtime state beyond pixel
  storage

## Affected Code

The main areas affected by this redesign are:

- `drafts/colors.h`
- `drafts/gamma.h`
- `drafts/gamma.cpp`
- `drafts/runtime_constants.h`
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
- `drafts/playback.h`
- `drafts/playback.cpp`
- `drafts/program_structs.h`
- `drafts/program_structs.cpp`
- `src/decoder.h` / `src/decoder.cpp`
- `src/animation.h`
- concrete animation headers, especially `anim_shift.h`
- `src/engine.h` / `src/engine.cpp`
- `src/compositor.h` / `src/compositor.cpp`
- `src/colors.h` / `src/colors.cpp`
- new `src/gamma.h` / `src/gamma.cpp`
- `compiler/elements/compiler.py`
- `compiler/elements/blob.py`

The draft source files are meant to make those changes easier to refine
without having to reconstruct the data model from discussion snippets.
