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
- `pixel_buffer_pool.h`
- `pixel_buffer_pool.cpp`
- `pixel_view.h`
- `pixel_view.cpp`
- `animation.h`
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

### `PixelView`

`PixelView` is a small non-owning class that wraps logical pixel access into a
real backing buffer.

It contains:

- a pointer to the backing `hsva_t` buffer
- an optional index indirection array
- a logical length

Its main API is indexed access:

- `view[i]` returns the correct pixel in the underlying buffer
- identity views avoid extra metadata beyond the null index pointer

This keeps the animation-facing code simple. Animations see a logical pixel
space; `PixelView` hides whether that space is direct or reindexed.

In the current draft code, runtime `PixelView`s are built from decode-time
`PixelViewDef` records. A `PixelViewDef` is not exposed to animations; it is
only decoder metadata describing how to bind a runtime `PixelView` onto a real
pool buffer.

### Three-view event model

Each event references up to three `PixelView`s:

- `src_pixv_idx`
  - optional
  - read-only input used during `initialize()`
- `dst_pixv_idx`
  - required
  - render target for `render()`
- `work_pixv_idx`
  - optional
  - persistent mutable storage used by stateful animations

This is the key semantic split:

- `src` is initialization input
- `dst` is current-frame output
- `work` is event-local storage that remains valid for the lifetime of the
  active event

That model keeps shift correct without reintroducing today's coordinate bridge.

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

## Layer Buffers And Compositor

`PixelView` is for animation access. The compositor still composites canonical
layer buffers onto the physical strip.

That means:

- animations write through `PixelView`s
- those views land in real backing buffers from `PixelBufferPool`
- each layer still has one canonical display buffer
- each layer still has a physical LED mapping (`physical_map`)
- the compositor still iterates the whole layer buffer and maps each slot to a
  physical LED before blending

So `PixelView` does not replace the compositor's layer model. It replaces the
animation-side routing model.

## Compiler / Decoder Responsibilities

### Compiler

The compiler owns:

- layer inference and layer ordering
- allocation/reuse of real HSVA buffers
- generation of the ordered real-buffer size list
- generation of `PixelView` descriptors
- assigning `src_pixv_idx`, `dst_pixv_idx`, and `work_pixv_idx` per event
- validating that work-storage reuse is safe

Important reuse rules:

- same-layer event reuse remains valid because layer events do not overlap
- work-buffer reuse is allowed when lifetimes do not overlap
- partial reuse is allowed only when overlapping regions are proven disjoint

### Decoder

The decoder owns:

- allocating `PixelBufferPool`
- building the resolved `PixelView[]` table from the compiler's descriptors
- constructing concrete animation objects from decoded params
- wiring layer buffers, views, and events together into `Program`

The decoder should not need animation-type-specific memory policy. It should
mostly consume indices, sizes, and descriptors emitted by the compiler.

## Memory Model

The design objective is to keep HSVA memory allocation compact and predictable.

The intended direction is:

- the compiler emits an ordered list of real buffer sizes
- the decoder allocates the pool once
- each pool buffer is resolved by index
- `PixelView`s point into those resolved real buffers

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

The engine is responsible for ensuring:

- `initialize()` runs before the first `render()` of an activation
- after reset / jump / restart, the event is treated as uninitialized again
- buffers that must start cleared are cleared by engine/program reset

The current leaning is to keep this lifecycle state in the engine, not inside
the animation base class.

## What This Replaces

The intended simplifications are:

- `AnimParams` no longer remain inert until activation; decoder constructs
  `Animation` objects directly
- engine-side `create_animation()` goes away
- event-side `remap` / `remap_is_identity` / `remap_length` go away
- `temp_buffer` goes away
- the current shift coordinate bridge goes away
- today's shift work-buffer pool becomes a more general `PixelBufferPool` +
  `work_pixv_idx` model

## Things To Keep Explicit

These points should stay explicit in the design:

- `dst` is mandatory; `src` and `work` are optional
- `work` is persistent event-local pixel storage, not just a scratch pointer
- `PixelView` is non-owning
- the compiler owns buffer reuse correctness
- the compositor still works from canonical layer buffers, not arbitrary views

## Open Items

These areas are intentionally not locked in yet:

- exact blob layout for `PixelBufferPool` and `PixelView` descriptors
- whether pool internals are represented as one contiguous block, a small set
  of chunks, or another pool implementation detail
- whether `PixelView` should expose any helpers beyond `operator[]`, `size()`,
  and `clear()`
- future animation types that may need additional runtime state beyond pixel
  storage

## Affected Code

The main areas affected by this redesign are:

- `drafts/colors.h`
- `drafts/pixel_buffer_pool.h`
- `drafts/pixel_buffer_pool.cpp`
- `drafts/pixel_view.h`
- `drafts/pixel_view.cpp`
- `drafts/animation.h`
- `drafts/program_structs.h`
- `drafts/program_structs.cpp`
- `src/decoder.h` / `src/decoder.cpp`
- `src/animation.h`
- concrete animation headers, especially `anim_shift.h`
- `src/engine.h` / `src/engine.cpp`
- `src/compositor.h` / `src/compositor.cpp`
- `compiler/elements/compiler.py`
- `compiler/elements/blob.py`

The paired sketch files are meant to make those changes easier to refine
without having to reconstruct the data model from discussion snippets.
