# Core Animation Data Model

Runtime-facing redesign of the data structures that connect the compiler, the
decoder, and the engine.

The compiler-side safety contract lives in `compiler.md`. The decoder's
per-program factory responsibilities live in `decoder.md`. The playback state
machine lives in `playback.md`. This file is the runtime data story and the
canonical home for the time-naming vocabulary.

## Goal

- drop the engine's two-stage pixel routing (`remap` + `index_map`)
- drop the runtime animation factory; the decoder constructs concrete
  animations directly
- keep stateful animations such as shift correct
- keep HSVA allocation compact and predictable on ESP32
- keep compiler and decoder responsibilities separable

## Old → New

| Old                                              | New                                                                 |
|--------------------------------------------------|---------------------------------------------------------------------|
| event `remap` / layer `index_map`                | one `PixelView` per logical use, with optional storage + physical maps |
| engine `create_animation()` factory              | decoder constructs animations from decoded params                   |
| inert `AnimParams` carried into runtime          | event holds a concrete `Animation*`                                 |
| shift work-buffer pool allocated separately      | `PixelBufferPool` with `work_pixv_idx` per event                    |
| `Program::temp_buffer` + per-layer `LayerDef::buffer` | explicit `PixelView`s in a shared pool                         |
| `active_mask`                                    | `active_dst_views[layer]` — null means inactive                     |
| implicit source-layer dependency                 | explicit `src_pixv_idx` on events, with `CopyOp` records when preservation needs a move |

## Components

Concrete APIs live in `drafts/*.h`. What follows is the shape and the
contract, not the signature list.

### `PixelBufferPool` (`pixel_buffer_pool.h`)

Owns the real `hsva_t` backing buffers named by the compiler. Build-mode
allocation strategy differs — see §Memory Model. The public API stays small:
initialize from an ordered size list, resolve a buffer by index, report its
size, release on teardown.

### `PixelView` (`pixel_view.h`)

Wraps logical pixel access over a pool buffer. Two independent mappings:

- **storage mapping** — where logical pixels live in HSVA memory
  (`view[i]`, used by animations and copy ops)
- **physical mapping** — where logical pixels land on the strip
  (`view.physical_index(i)`, used by the compositor)

The dominant case is identity storage. Physical mapping is required only on
destination views that may reach the compositor; source, work, and copy-only
views can be storage-only.

`PixelView` does not own its backing buffer (the pool does) but does own its
optional index metadata, so view lifetime is self-contained.

### `PixelViews` (`pixel_views.h`)

Owns the runtime `PixelView[]` table. Built once from `PixelViewSpec[]` — an
input-only construction record emitted by the compiler and consumed by the
decoder; not retained in `Program`.

### Three-view event model (`layer.h`, `animation.h`)

Each `AnimationEvent` references up to three views:

- `src_pixv_idx` — optional, read-only input to `initialize()`
- `dst_pixv_idx` — required, the render target for `render()`; must have a
  physical mapping
- `work_pixv_idx` — optional, persistent mutable storage for stateful
  animations

Animation interface:

- `initialize(const PixelView* src, PixelView* work)`
- `render(PixelView& dst, float t_animation)` — must fully define every
  logical pixel in `dst` on every frame

### `CopyOp` (`copy_ops.h`)

Internal preservation operation, not a visual event and not a layer. Each
entry carries `at` (program-relative seconds), `src_pixv_idx`, and
`dst_pixv_idx`. The engine runs due copy ops before visual rendering for the
frame.

Copy ops move already-rendered pixels to a stable location so a later event
can read them. They do not re-evaluate an animation. The compiler is
responsible for scheduling each copy at a time when the source view actually
contains the intended pixels (typically source-end or just before a scratch
reuse); see `compiler.md`.

### `Layer` (`layer.h`)

A timeline of visual events. Answers only: which event is active at time
`t`? Layers no longer hold HSVA buffers, index maps, or physical maps.

### `Engine` (`engine.h`)

Owns `Compositor` internally. Owns per-layer playback state (event cursor +
initialized flag), a monotonic copy-op cursor, and a reused
`active_dst_views[]` array (one entry per layer).

`render_frame(t_program, Strip&)` order:

1. run due copy ops with `op.at <= t_program`
2. clear `active_dst_views[]` to `nullptr`
3. walk layer timelines, find the active event per layer
4. initialize newly-active events with optional `src` / `work`
5. render each active event into its `dst` view
6. store the active `dst` view in `active_dst_views[layer]`
7. composite into `Strip`

Layer activity is carried by the active-view pointer; there is no parallel
`active_mask`. After reset / jump / restart, events are treated as
uninitialized again.

### `Compositor` (`compositor.h`)

`composite(Strip&, PixelView* const* active_dst_views, uint8_t count)`. No
layer headers, no cursors, no buffer indirection — just `view[i]` +
`view.physical_index(i)` per contributing layer in layer order.

### `Strip` (`strip.h`)

The canonical linear-RGB frame buffer. Owns exact-sized RGB storage sized
from `HardwareProfile::strip_length`. Rendering always produces linear RGB
in `Strip`; sim, tests, and debug output read that directly. Hardware-facing
transforms (gamma, channel order) happen afterward — see §Output Transforms.

## Why `work` Exists

Stateful animations need mutable storage distinct from the current render
target. Shift is the motivating case: on first activation it snapshots
source pixels into `work`; on each frame it reads `work` and writes `dst`.

`work` must not alias mutable output storage for animations that need a
stable snapshot. The compiler is responsible for assigning valid work
storage for the lifetime of each event.

## Output Transforms

Gamma and channel order sit outside `Engine` and `Compositor`:

- `apply_gamma(Strip&, const GammaCorrection&)` — in-place on the Strip
- `Strip::copy_to(dst, ColorOrder)` — last-mile copy for hardware sinks

`GammaCorrection` is a caller-owned LUT. Default-constructed is identity, so
sim/tests/debug output share the render path without a separate flag.
`set_gamma()` accepts `1.0` (identity) and `(1.0, kMaxSupportedGamma]`;
invalid values leave the previous LUT in place. WS2812 dark-room starting
point is `kWs2812DarkRoomGamma = 2.8`.

`ColorOrder` is limited to `RGB` and `BGR` until a real strip requires
more.

## Memory Model

HSVA memory flows through one ordered list:

- compiler emits the ordered real-buffer size list
- decoder allocates the pool once, resolves by index
- each runtime `PixelView` points into one resolved pool buffer
- events and copy ops reference views by index

Build-mode split inside `PixelBufferPool`:

- `ARDUINO` — one compact pooled allocation; minimizes ESP32 fragmentation
- non-`ARDUINO` — one allocation per logical buffer; keeps Valgrind
  effective at catching inter-buffer overruns

Public pool API is identical in both modes. Slices are taken only on
`hsva_t` boundaries, so no extra alignment logic is needed; layout
assumptions are asserted in `drafts/colors.h` and will land in
`src/colors.h` with the rename pass.

## Time Naming Policy

These rules apply to all runtime code. `synced_clock.md`, `playback.md`, and
this document link to this section as the source of truth.

Rules:

- loose values, parameters, locals, and returns use strict domain names
- `t_<domain>` means float seconds relative to that domain
- `<thing>_us` / `<thing>_ns` means an absolute timestamp or delta in the
  named units
- delta names encode direction/sign at public API boundaries unless the
  sign convention is pinned nearby (docstring or enclosing type)
- struct fields may use short contextual names when the struct type pins
  meaning and units

Runtime vocabulary:

| Name                                       | Meaning                                                                    |
|--------------------------------------------|----------------------------------------------------------------------------|
| `t_program`                                | float seconds relative to program start                                    |
| `t_animation`                              | float seconds relative to animation/event start                            |
| `program_start_us`                         | absolute program start in the selected clock domain                        |
| `program_clock_now_us()`                   | absolute now in the selected program clock domain                          |
| `now_remote_us()` / `now_local_us()`       | absolute remote / local-monotonic timestamps                               |
| `SyncedClock`                              | synced-clock abstraction exposing remote and local domains                 |
| `is_synced()`                              | status predicate for remote-domain validity                                |
| `apply_sync_offset(offset_us, valid_for_us)` | applies a sync-offset lease; `offset_us` = local minus remote             |
| `render_next_frame()`                      | accepts the next renderable frame from the selected clock and state       |
| `RenderFrameResult`                        | presentation-side result from a playback call that may update `_strip`    |
| `current_t_program()`                      | current program-time cursor                                                |
| `_t_program_cursor_us`                     | minimum accepted program position, integer microseconds                    |
| `target_fps`                               | program-level intended presentation cadence, in Hz                         |

Contextual struct fields:

| Field                       | Meaning                        |
|-----------------------------|--------------------------------|
| `AnimationEvent::start`     | program-relative float seconds |
| `AnimationEvent::duration`  | float seconds                  |
| `CopyOp::at`                | program-relative float seconds |
| `Program::duration`         | float seconds                  |
| `Program::target_fps`       | intended cadence, Hz           |

Frame, reporting, and protocol names use the same vocabulary:
`DeviceFrame::t_program`, `ProgramFrame::t_program`, UDP frame header
`t_program`, start/resume `program_start_us`, jump target `t_program`. The
live tree may still contain legacy `t_rel` / `t0_us` names until the rename
pass lands.

Compiler-side Python may keep `_sec` suffixes because compiler code also
handles beat-space values — see `compiler.md`.

## Open Items

- compiler policy on preserving by stable dst buffer vs. explicit copy op —
  see `compiler.md`
- future animation types that may want runtime state beyond pixel storage

Blob bytes, parser shape, and decoder integration live in `blob_format.md`,
`decoder.h` / `decoder.cpp`, and `decoder.md` respectively.

## Affected Code

- `src/decoder.{h,cpp}` — replaced by `drafts/decoder.{h,cpp}`
- `src/animation.h` and concrete animation headers, especially
  `anim_shift.h`
- `src/engine.{h,cpp}`
- `src/compositor.{h,cpp}`
- `src/colors.{h,cpp}`
- new `src/gamma.{h,cpp}`
- `compiler/elements/compiler.py` — includes `_compute_required_starts`
  update to walk copy-op chains (see `compiler.md`)
- `compiler/elements/blob.py` — replaced by the v3 emitter matching
  `blob_format.md`
