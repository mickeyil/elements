# Compiler (v3)

What the Python compiler must become. The runtime side is implemented
(decoder, engine, copy ops, pixel views, playback in `src/`); the
compiler still emits v2 blobs. This doc specs the v3 compiler: what it
outputs, the analyses it runs, and the safety metadata that stays
controller-side. The byte layout is `docs/blob_format.md`, enforced by
`src/decoder.cpp`; structural caps are `src/blob_limits.h`; time
naming follows `data_model.md`, including its compiler-side `_sec`
exception.

## The ownership split

**Compiler proves, controller gates, runtime trusts.** The compiler
works out what is safe at compile time; the controller refuses
commands that would violate it; the device runs without defensive
checks. `safe_intervals` live in `CompiledManifest` (controller-side
metadata), never in the blob; the runtime has no concept of them.

## Output changes from v2

| v2                                                        | v3                                                           |
|-----------------------------------------------------------|--------------------------------------------------------------|
| `BLOB_VERSION = 2`                                        | `BLOB_VERSION = 3`                                           |
| Per-layer `index_map` (physical LED positions)            | Physical mapping on `PixelViewSpec`                          |
| Per-event `remap` / `remap_is_identity` / `remap_length`  | Storage mapping on `PixelViewSpec`                           |
| Per-event `source_layer` (u8, `0xFF` = none)              | Per-event `src_pixv_idx`, plus `CopyOp` records for movement |
| Shift-only `buffer_id`                                    | Per-event `work_pixv_idx` for any stateful animation         |
| (none)                                                    | `PixelViewSpec[]` records                                    |
| (none)                                                    | `CopyOp[]` records                                           |
| (none)                                                    | `target_fps` header field                                    |
| (none)                                                    | `requires_sync` header flag                                  |
| `temp_buffer` size + `max_remap_length`                   | Deleted; `PixelBufferPool` owns all HSVA storage             |
| `AnimParams` tagged union                                 | Deleted; each animation owns its param shape                 |
| `CompiledManifest.safe_intervals`                         | Unchanged home; now width-filtered (below)                   |

## Safe intervals

An event that reads an earlier event's output cannot safely start
unless that output was actually rendered. The compiler computes, per
program, the time ranges where rebuilding engine state from scratch is
safe, and the controller only aims state-rebuilding commands (`JUMP`,
rejoin via re-`LOAD`, `START` at nonzero time) into them.

Rules:

- **Width filter.** Drop candidate intervals narrower than one frame
  period (`1 / target_fps`); a valid-but-narrower interval cannot
  render even one frame before the next write lands.
- **Zero is special.** `t_program == 0` is kept out of the interval
  list so the width filter can never drop it; `START` at program start
  is always safe.
- **Headroom.** The controller leaves one frame period between its
  chosen target and the interval's end. The compiler filters widths;
  the controller owns headroom.
- **RESUME is not gated.** `Playback::handle_resume` keeps engine
  state intact (no reset), so resuming where paused is always correct.
  Real retiming goes through `JUMP`, which is gated.

To rejoin a device, the controller sends:

- Playing session: `LOAD`, `JUMP(safe_point)`,
  `RESUME(program_start_us)`; the device waits until the clock reaches
  the cursor.
- Paused session: the same without the final `RESUME`; the device
  waits at the safe point.
- If no safe interval remains in the current segment, that device
  cannot be safely rejoined.

Programs with long chains of events reading other events get shorter
safe intervals; that is the intended trade-off. Offline render walks
frames in order from 0 and never consults intervals.

## target_fps

One program-level value (default 50 Hz), emitted into every strip blob
header of the compiled program. Three decisions hang off it: the
safe-interval width filter, the controller's headroom rule, and
offline render's default frame step. The device does not refuse to
LOAD based on it; it runs the program regardless and reports render
slack as telemetry.

## requires_sync

One program-level flag, the same in every strip blob, emitted into the
header. The compiler only carries the declared intent; the device
enforces it at `START`/`RESUME`/`JUMP` (`src/playback.{h,cpp}`). How
the DSL declares it is TBD (expected: a program-level constant).

## Tracing source dependencies

`required_start_sec` for an event must reach back through everything
it reads, directly or through copies. v2 followed one kind of link
(`source_layer`); v3 follows two:

- an event writes its `dst_pixv_idx` for
  `[start, start + duration)` and stays that view's writer until the
  next one;
- a copy op writes its `dst_pixv_idx` at `at` with whatever was in its
  `src_pixv_idx` at that moment.

For an event `E` reading view `V`:

1. find the most recent writer of `V` at any time `<= E.start` (event
   or copy op);
2. if the writer is a copy op, continue from the writer of the copy
   op's own source view;
3. if the writer is an event `A`, the chain ends; take `A`'s
   `required_start_sec` (or `A.start` if `A` reads nothing);
4. `E.required_start_sec` is the earliest start found along the chain.

The same walk applies to `work_pixv_idx` when the work view starts
from preserved content rather than scratch. The "the data is simply
still there" case (nothing overwrote `V` since `A` wrote it) needs no
copy op: the walk ends at `A` directly.

## Emitting copy ops

Copy ops are the compiler's way to keep content alive: not visual
events, not layers, just data moves the engine runs before rendering
each frame. The decoder validates structure (sorted `at`, no two
same-`at` ops on one destination, view indices, equal sizes); the
following are compiler-only checks, impossible for the decoder because
events carry view indices rather than the identity of the event that
produced them:

- Schedule each op at a time when the source view actually holds the
  intended pixels (typically when the source ends, or just before its
  buffer is reused).
- The source must end at or before the dependent op's `at`;
  same-start or overlapping pairs are compile errors.
- Ops sharing one `at` are emitted so that a reader comes after its
  writer; cycles are compile errors.

## Buffer and view assignment

All HSVA storage is one compiler-planned pool. The compiler emits the
ordered buffer size list (`PixelBufferPool`), `PixelViewSpec[]`
records carrying the backing buffer index plus optional storage and
physical index arrays, and per-event / per-op view indices.

Reuse rules:

- Events on the same layer may share storage (layer events never
  overlap in time).
- Work buffers may be reused when lifetimes do not overlap.
- Copy-op destinations and preserved sources join the same lifetime
  analysis, and must not share storage with anything that overwrites
  it before every dependent has read it.
- Partial reuse of a buffer is allowed only when the regions are
  proven not to overlap.

## Caps and memory footprint

The decoder rejects blobs that exceed the caps in
`src/blob_limits.h`; the compiler should reject the same programs at
compile time, where the error is far easier to act on than a firmware
rejection. Mirror the cap values in Python, with a unit test that
parses `src/blob_limits.h` and asserts equality.

On every successful compile, print a firmware memory estimate from the
blob's own structures: pool bytes (buffer sizes x sizeof hsva), view
metadata plus index arrays for non-identity views, copy-op records,
layer events, and a per-animation-type instance estimate. Allocator
overhead and fragmentation are deliberately ignored; the estimate
guides optimization, not precise budgeting.

## Open items

- **Keep in place or copy.** When a dependency can be satisfied either
  by leaving the content where it is (nothing overwrites it) or by
  copying it aside, the choice is the compiler's. First-pass default:
  prefer the explicit copy when in doubt; extra HSVA is cheaper than
  tight reuse that is hard to prove correct.
- **Controller-visible metadata.** Whether `target_fps` and
  `requires_sync` reach the controller as `CompiledManifest` fields or
  by parsing the blob header. TBD.
- **Width-filter placement.** Per-strip, after global intersection, or
  after a scene-level merge. TBD.
- **Scene-level merges.** How a scene sequencing programs with
  different `target_fps` or `requires_sync` values resolves them. TBD
  with the scene/playlist surface.
