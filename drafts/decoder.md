# Decoder

Background and integration notes for the v3 blob decoder. The byte contract
is in `blob_format.md`. The parser API is in `decoder.h`.

## Why it exists

The legacy `src/decoder.h` / `src/decoder.cpp` mixes three roles: wire
constants, decoded runtime structs, and the parser itself. The redesign
splits those out.

| File                          | Owns |
| ----------------------------- | ---- |
| `src/decoder.h`               | `decode_program()`, `BLOB_VERSION`, `BLOB_MAGIC` |
| `src/decoder.cpp`             | the decode logic |
| `src/blob_reader.h`           | `BlobReader`, `DecodeError`, `decode_error_name()` (declarations) |
| `src/blob_reader.cpp`         | `BlobReader` and `decode_error_name()` implementations |
| `src/blob_limits.h`           | `MAX_*` cap constants |
| `src/animation_types.h`       | `AnimType` enum |
| `src/program.h`               | `Program` shape, `free_program()` |
| `drafts/layer.h`              | `Layer`, `AnimationEvent` |
| `drafts/pixel_views.h`        | `PixelViews`, `PixelViewSpec` |
| `drafts/pixel_buffer_pool.h`  | `PixelBufferPool` |
| `drafts/copy_ops.h`           | `CopyOps`, `CopyOp` |
| `src/animations/wave.h` (etc.) | per-animation params struct AND its `Animation` subclass |

`src/blob_reader.h` exists separately from `decoder.h` so per-animation
`anim_*.h` headers can include it (for `BlobReader` and `DecodeError`)
without dragging in the full decoder entry point. The decoder includes both;
animation headers include only `blob_reader.h`.

The decoder is the only translation unit that includes every `anim_*.h`.
Each animation header is otherwise self-contained.

`src/blob_limits.h` and `src/animation_types.h` are tiny one-screen
headers. `blob_limits.h` reuses `MAX_STRIP_PIXELS` from
`src/hardware_profile.h` and defines the rest of the caps from
`blob_format.md`. `animation_types.h` is just the `AnimType` enum.

## Decoder responsibilities

- allocate `PixelBufferPool` from the buffer-size table
- build `PixelViews` from `PixelViewSpec` records
- build `CopyOps` from `CopyOp` records
- construct concrete `Animation` instances directly into events
- wire layers, events, views, and copy ops into a `Program`
- populate `Program::requires_sync` from the header flag
- populate `Program::target_fps` from the header field
- enforce every validation rule in `blob_format.md`

The decoder does **not** own:

- HSVA memory layout policy (`PixelBufferPool` owns this)
- runtime activation state (`Engine` owns this)
- animation execution (each animation subclass owns this)
- topological ordering of same-`at` copy ops by data dependency. The
  decoder trusts the compiler-emitted blob order for same-`at` execution
  and only rejects the cheapest-to-detect violation: two same-`at` copy
  ops writing the same `dst_pixv_idx` (ambiguous aliasing). Cycle
  detection and full topological validation are compiler-side.
- source-dependency validation. v3 events carry view indices, not
  source-event identity, so the decoder cannot verify that a source
  dependency resolves only to a writer whose end is `<=` the dependent's
  start. That semantic check is compiler-only; see `compiler.md`.

## Single-pass shape

`decode_program` is one forward pass:

1. Validate magic and version. **Reject before any allocation.**
2. Parse the rest of the header into a stack-local struct.
3. Validate header: reserved flag bits, `target_fps` non-zero, count caps,
   `strip_length` non-zero and within `MAX_STRIP_PIXELS`, `duration` finite
   and > 0.
4. Validate `strip_length` against the profile (`StripLengthMismatch`).
5. Allocate `Program`. The struct is small; allocating it first means every
   subsequent failure path uses one uniform cleanup (`free_program(prog)`).
6. Read the buffer-size table into a temporary, validate per-buffer caps
   and the global `MAX_POOL_BYTES`, then initialize `PixelBufferPool` from
   the temporary.
7. Read and initialize `PixelViews`.
8. Read and initialize `CopyOps`.
9. For each layer: read `event_count`, then for each event read fixed
   fields, validate view indices and float ranges, read `params_size`
   bytes, dispatch on `anim_type` to the per-animation factory.
10. Require `reader.done()`. If bytes remain, return `TrailingBytes`.

## Animation construction

Each animation header exposes a static factory that owns its own param shape:

```cpp
class Wave : public Animation {
public:
    static Animation* from_blob(const uint8_t* params, size_t params_size,
                                DecodeError* err_out);
    // ...
};
```

Contract:

- returns a constructed subclass on success; `*err_out` is set to
  `DecodeError::Ok`
- returns `nullptr` on failure; `*err_out` is set to `InvalidField` for
  malformed param bytes or `OutOfMemory` for allocation failure
- `err_out` is never null when called by the decoder
- the factory **must** reject NaN, ±Inf, and out-of-range values in its
  own params (the spec's "all floats finite" rule binds factories too,
  not just the decoder)
- the factory **may** consume fewer than `params_size` bytes; trailing
  bytes are reserved for forward-compatible param extensions. Format
  changes that alter required semantics bump the blob version, not extend
  params silently
- the factory builds its own local `BlobReader` over the params slice;
  it does not need to include `decoder.h`, only `blob_reader.h`

The decoder dispatches:

```cpp
DecodeError perr = DecodeError::Ok;
switch (static_cast<AnimType>(anim_type)) {
    case AnimType::Wave:  event.animation = Wave::from_blob(p, n, &perr); break;
    case AnimType::Shift: event.animation = Shift::from_blob(p, n, &perr); break;
    case AnimType::Spark: event.animation = Spark::from_blob(p, n, &perr); break;
    case AnimType::Paint: event.animation = Paint::from_blob(p, n, &perr); break;
    default:              return DecodeError::InvalidField;
}
if (event.animation == nullptr) return perr;
```

### Per-anim post-checks against the resolved event

A few animation types carry constraints that can only be checked once the
event's view indices are resolved. The decoder runs these after `from_blob`
returns:

- **`AnimType::Paint` constant mode.** The constant array's length must
  equal the dst view's size. `Paint` cannot self-validate because
  `from_blob` does not see the dst view; the decoder calls
  `paint->constant_array_size()` and rejects `InvalidField` on mismatch.
  Solid-mode paint reports `0` and trivially passes the check.

- **`AnimType::Shift`.** The event must carry `src_pixv_idx != PIXV_NONE`.
  Shift snapshots from src in `initialize()`; without a source view the
  work buffer would be all zeros (after `Engine::reset()`) or stale
  data (otherwise), and the rendered output would not be meaningful.
  The decoder rejects `InvalidField` when the source index is missing.

Each `from_blob` parses its own param bytes (using a local `BlobReader` over
the params slice) and constructs the subclass. The decoder never sees an
animation-specific param shape. The legacy `AnimParams` tagged union is
deleted.

## Failure mapping

`decode_program` returns `nullptr` on any failure and writes the category
into `*err_out`. The firmware caller (`ControllerConnection::handle_load_`)
maps `DecodeError` to ACK codes:

| `DecodeError`         | ACK                       |
| --------------------- | ------------------------- |
| `Ok`                  | `kAckOk`                  |
| `StripLengthMismatch` | `kAckProfileMismatch`     |
| anything else         | `kAckError`               |

Every failed load logs `decode_error_name(err)` so serial logs identify the
exact rejection reason without a debugger.

## Integration with Playback

`Playback::handle_load(blob, blob_len, gen)` is the runtime caller of
`decode_program`:

```cpp
DecodeError err = DecodeError::Ok;
Program* program = decode_program(blob, blob_len,
                                  _profile.strip_length, &err);
if (program == nullptr) {
    clear_render_buffer_();
    // owner logs decode_error_name(err)
    return false;
}

// Snapshot Program-level fields before handing ownership to Engine. After
// Engine::create() the Program pointer (whether the call succeeds or not)
// belongs to Engine, never to Playback directly.
const float duration_s = program->duration;
const uint8_t target_fps = program->target_fps;
const bool  needs_sync = program->requires_sync;

Engine* engine = Engine::create(program);  // takes ownership of program
if (engine == nullptr) {
    clear_render_buffer_();
    // owner logs "[load] engine alloc failed" — distinct from decode errors
    return false;
}

_duration      = duration_s;
_target_fps    = target_fps;
_requires_sync = needs_sync;
_engine.reset(engine);
_state = DeviceState::LOADED;
return true;
```

`requires_sync` and `target_fps` are read once at load time and stored on
`Playback`. They are not re-read during playback. `target_fps` is exposed so
the owner can pace presentation and report cadence/slack telemetry; the engine
and playback core do not use it as a hot-path time step.

`Engine::create()` is a fallible factory: it owns the `Program` on both
success and failure (frees it via `free_program()` if Engine itself or
its internal allocations cannot be made). Engine OOM is **not** a
`DecodeError` — the blob decoded fine; the device just ran out of heap
mid-load. The firmware caller should log it under a separate identifier
and ACK the LOAD command with `kAckError` (the same generic bucket as
non-`StripLengthMismatch` decode failures).

Tooling that only needs the render core may also call `decode_program`
directly. In particular, `strip_render` should decode the blob, reject
`Program::requires_sync`, create `Engine`, and drive
`Engine::render_frame(t_program, strip)` with explicit frame times instead of
constructing `Playback`.

## Compiler alignment

The Python compiler must:

1. **Hardcode the same cap values** as `blob_limits.h`, with a unit test that
   parses the C header and asserts equality.
2. **Emit `target_fps`** as a program-level cadence field. The default is
   50 Hz unless the program overrides it. The compiler also uses this value
   when computing safe intervals: any nonzero candidate safe interval narrower
   than one target frame period is dropped, while the `t_program == 0` start
   sentinel is preserved separately from the nonzero interval list.
3. **Fail the build** with a clear error when any cap is exceeded.
   Compile-time failure is much easier to triage than firmware rejection.
4. **Print an estimated firmware memory footprint** for every successful
   compile, computed from the in-blob structures:

   - HSVA pool bytes = sum of buffer sizes × 16
   - PixelView metadata = `pixel_view_count` × per-view fixed overhead, plus
     the storage_indices and physical_indices arrays for non-identity views
   - CopyOp records = `copy_op_count` × `sizeof(CopyOp)`
   - Layer events = sum over layers of `event_count` × `sizeof(AnimationEvent)`
   - Animation instances = sum over events of the per-anim-type sizeof
     estimate

   Allocator overhead, internal heap fragmentation, and stack costs are
   intentionally ignored. The estimate is for guiding optimization, not for
   precise budgeting.

## Required edits to existing drafts

The new decoder depends on a few small additions elsewhere:

- `src/firmware/wire_constants.h` — add `kAckProfileMismatch = 3`.
- `drafts/playback.cpp::handle_load` — replace the placeholder body with
  the integration sketch above.

`src/program.{h,cpp}` already carry the supporting changes:
`Program::requires_sync` is declared, `Program::~Program()` releases the
layers array, and `free_program(Program*)` is declared there as a thin
wrapper around `delete prog`. The decoder does not redeclare
`free_program`; callers that need it include `program.h`.

## What this replaces

- `src/decoder.h` and `src/decoder.cpp` — replaced wholesale.
- `BLOB_VERSION = 2` — replaced by `BLOB_VERSION = 3`.
- `AnimParams` tagged union and `WaveParams`/`ShiftParams`/`SparkParams`/
  `PaintParams` in the legacy header — deleted; each animation owns its own
  params shape next to its class definition.
- `LayerDef`, `BufferPool`, the legacy `AnimationEvent`, the legacy
  `Program` — deleted; their replacements live in `src/program.*`,
  `src/layer.*`, `src/pixel_*.*`, and `src/copy_ops.*`.
- `max_remap_length` in the header and `temp_buffer` in `Program` — deleted
  with the old scatter-copy path.

## Known gaps (deferred)

Both items below are real correctness gaps in the post-step-19 decoder. They
sit on the validation boundary -- the right place to fix is the decoder, not
the runtime. Tackle as a separate task; tests should be added alongside the
fixes in `test/test_decoder.cpp`.

### 1. Paint constant-mode `count == 0` accepted, then `render()` dereferences nullptr

`AnimPaint::from_blob` accepts a `mode == 1, count == 0` event. It allocates
no constant array (`_constant = nullptr`) but still sets `_mode = Constant`.
The decoder's per-anim post-check is:

```cpp
const uint8_t k = paint->constant_array_size();
if (k != 0 && k != prog.pixel_views.at(dst_pixv_idx).size()) {
    /* reject */
}
```

`constant_array_size()` returns `_mode == Constant ? _constant_count : 0`,
so a `count == 0` constant-mode paint and a solid-mode paint are
indistinguishable from outside. The `k != 0` guard then skips the check.
Engine activates the event, and `Paint::render()` enters the constant-mode
loop and dereferences `_constant[i]` from `nullptr` for every dst pixel
(when `dst.size() > 0`). Crash.

**Fix sketch.** Split the accessor so the decoder can ask the two questions
independently:

```cpp
// src/animations/paint.h
bool is_constant_mode() const { return _mode == Mode::Constant; }
uint8_t constant_array_size() const { return _constant_count; }
```

Decoder check becomes:

```cpp
if (paint->is_constant_mode()
    && paint->constant_array_size() != prog.pixel_views.at(dst_pixv_idx).size()) {
    delete anim;
    return DecodeError::InvalidField;
}
```

This catches all four cases:

| `count` | `dst.size()` | result                                    |
|---------|--------------|-------------------------------------------|
| 0       | 0            | accepted (both empty; render no-op)       |
| 0       | > 0          | rejected (`InvalidField`)                 |
| N       | N            | accepted                                  |
| N       | != N         | rejected                                  |

**Test additions:** decoder rejects `mode=1, count=0` against a non-empty
dst view; decoder accepts `mode=1, count=0` against a zero-size dst view
(if the empty-view shape is otherwise legal).

### 2. Shift requires src, but not work

The decoder rejects shift events with `src_pixv_idx == PIXV_NONE`, but does
not require `work_pixv_idx`. With `work_pixv_idx == PIXV_NONE`, Engine
passes `nullptr` to `Shift::initialize`, `_work` stays null, and
`Shift::render()` returns early without writing dst. This violates the
Animation contract that every dst pixel be defined per frame, and on a
fresh activation `dst` ends up showing whatever was there last.

Two related compiler-emit invariants the decoder also doesn't enforce:

- `work.size() == 0` would trip `((src_i % work_len) + work_len) % work_len`
  in the circular branch with `work_len == 0` -- division by zero.
- `src.size() != work.size()` produces a partial snapshot:
  `Shift::initialize` copies `min(src.size(), work.size())` pixels, leaving
  the tail of `work` at whatever it was (zero post-`Engine::reset()`,
  stale otherwise). Render then reads that tail when shifting.

**Fix sketch.** Replace the current shift post-check with:

```cpp
if (type == AnimType::Shift) {
    if (src_pixv_idx == PIXV_NONE || work_pixv_idx == PIXV_NONE) {
        delete anim;
        return DecodeError::InvalidField;
    }
    const uint16_t src_size  = prog.pixel_views.at(src_pixv_idx).size();
    const uint16_t work_size = prog.pixel_views.at(work_pixv_idx).size();
    if (src_size == 0 || work_size == 0 || src_size != work_size) {
        delete anim;
        return DecodeError::InvalidField;
    }
}
```

**Test additions:** decoder rejects shift with no work view; with
`work.size() == 0`; with `src.size() != work.size()`.
