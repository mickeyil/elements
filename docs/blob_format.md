# Blob Format

Binary format for compiled animation programs. The compiler emits one;
firmware loads it and runs it.

## Structure

A blob is a sequence of fixed sections, byte-packed (no padding):

```
┌────────┬──────────────┬─────────────┬──────────┬─────────────────┐
│ header │ buffer sizes │ pixel views │ copy ops │ layers + events │
│ (20 B) │              │             │          │                 │
└────────┴──────────────┴─────────────┴──────────┴─────────────────┘
```

Multi-byte values are little-endian. All floats must be finite — NaN and
±Inf are rejected as `InvalidField`.

## Header (20 bytes)

| Offset | Size | Type    | Field            |
|--------|------|---------|------------------|
| 0      | 4    | char[4] | magic            |
| 4      | 1    | u8      | version          |
| 5      | 1    | u8      | flags            |
| 6      | 1    | u8      | target_fps       |
| 7      | 1    | u8      | layer_count      |
| 8      | 2    | u16     | strip_length     |
| 10     | 2    | u16     | buffer_count     |
| 12     | 2    | u16     | pixel_view_count |
| 14     | 2    | u16     | copy_op_count    |
| 16     | 4    | f32     | duration         |

- `magic` — `"ELEM"` (`0x45 0x4C 0x45 0x4D`).
- `version` — `3`.
- `flags` — bit0 = `requires_sync`. All other bits reserved (must be 0).
- `target_fps` — intended frame rate in Hz, used as a pacing hint. Must
  be > 0. Devices may run slower; this is not a capability requirement.
- `strip_length` — physical LED count this blob targets. Must equal the
  active hardware profile's strip length (otherwise
  `StripLengthMismatch`).
- `duration` — total program duration in seconds. Must be > 0.

The four count fields (`layer_count`, `buffer_count`, `pixel_view_count`,
`copy_op_count`) bound the sections that follow. Each is checked against
its cap (see Caps) before any allocation.

## Buffer sizes

`buffer_count` u16 entries, in pool index order. Each is the pixel count
of one HSVA pool buffer; backing memory is `size * 16` bytes. The total
across all buffers must not exceed `MAX_POOL_BYTES`.

## Pixel views

`pixel_view_count` descriptors. Each descriptor is a 5-byte prefix
followed by zero, one, or two optional index arrays:

```
[u16 buffer_idx] [u16 size] [u8 flags]
[u16[size] storage_indices]    if !storage_identity
[u16[size] physical_indices]   if has_physical && !physical_identity
```

Flags:

- bit0 — `storage_identity`: `view[i]` maps to `buffer[i]`.
- bit1 — `has_physical`: this view has a physical LED mapping and may
  serve as an event `dst` view.
- bit2 — `physical_identity`: physical map is identity (`view[i]` → LED
  `i`). Only meaningful when `has_physical` is set.

Bits 3–7 are reserved (must be 0). A view with `has_physical=false` and
`physical_identity=true` is malformed.

When `has_physical && physical_identity`, `size` must be ≤ `strip_length`.

`storage_indices[i]` is a slot index into `pool_buffer[buffer_idx]`.
`physical_indices[i]` is an LED index in `[0, strip_length)`.

## Copy ops

`copy_op_count` records, sorted ascending by `at`:

| Size | Type | Field        |
|------|------|--------------|
| 4    | f32  | at           |
| 2    | u16  | src_pixv_idx |
| 2    | u16  | dst_pixv_idx |

`at` must be in `[0, duration)`. Both indices must reference valid views
of equal `size`; neither may be `PIXV_NONE`.

Same-time copy ops execute in blob order. The compiler places them in a
topological order that respects data dependencies, and rejects cycles or
two same-time ops writing to the same destination view.

## Layers and events

`layer_count` layers. Each layer is:

```
[u16 event_count] [event_count events]
```

Events on a layer must be sorted ascending by `start` and must not
overlap.

Each event:

| Size | Type | Field         |
|------|------|---------------|
| 1    | u8   | anim_type     |
| 4    | f32  | start         |
| 4    | f32  | duration      |
| 2    | u16  | src_pixv_idx  |
| 2    | u16  | dst_pixv_idx  |
| 2    | u16  | work_pixv_idx |
| 2    | u16  | params_size   |
| N    | u8[] | params        |

- `anim_type` — `AnimType` enum value (see `animation_types.h`).
- `start` — program-relative seconds. Must be ≥ 0.
- `duration` — seconds. Must be > 0. `start + duration` ≤ program
  `duration`.
- `src_pixv_idx` / `work_pixv_idx` — `PIXV_NONE` (`0xFFFF`) when absent;
  otherwise must be a valid pixel view index.
- `dst_pixv_idx` — required, must be a valid pixel view index, and the
  referenced view must have `has_physical=true`.
- `params` — animation-type-specific bytes; the layout lives in each
  `anim_*.h`. A factory may consume fewer than `params_size` bytes
  (trailing bytes are reserved for forward-compatible extensions);
  changes that affect semantics must bump the blob version.

## Caps

| Constant               | Value   | Bounds                                   |
|------------------------|---------|------------------------------------------|
| `MAX_LAYER_COUNT`      | 32      | header `layer_count`                     |
| `MAX_STRIP_PIXELS`     | 1000    | header `strip_length`, per-buffer `size` |
| `MAX_BUFFER_COUNT`     | 256     | header `buffer_count`                    |
| `MAX_PIXEL_VIEW_COUNT` | 512     | header `pixel_view_count`                |
| `MAX_COPY_OP_COUNT`    | 512     | header `copy_op_count`                   |
| `MAX_EVENTS_PER_LAYER` | 1024    | per-layer `event_count`                  |
| `MAX_PARAMS_BYTES`     | 4096    | per-event `params_size`                  |
| `MAX_POOL_BYTES`       | 100 KiB | sum of `size * 16` over all pool buffers |

These constants live in `src/blob_limits.h` and are mirrored by the
compiler.

## Rejection reasons

| `DecodeError`         | Cause                                                           |
|-----------------------|-----------------------------------------------------------------|
| `BadMagic`            | first 4 bytes are not `"ELEM"`                                  |
| `BadVersion`          | version byte is not 3                                           |
| `Truncated`           | a read would advance past the end of the buffer                 |
| `TrailingBytes`       | parser finished before the end of the buffer                    |
| `InvalidField`        | out-of-range index, bad flags, unsorted records, NaN/±Inf, etc. |
| `OverCap`             | any cap above is exceeded                                       |
| `StripLengthMismatch` | header `strip_length` ≠ active profile strip length             |
| `OutOfMemory`         | allocation failed during decode                                 |

`BadMagic` and `BadVersion` are checked before any allocation. Count
caps are checked before allocating their section. `MAX_POOL_BYTES` is
checked after reading buffer sizes, before pool allocation.
