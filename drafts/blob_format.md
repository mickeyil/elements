# Blob Format v3

Binary format for compiled animation programs. Emitted by the Python compiler
(`compiler/elements/blob.py`), consumed by the firmware decoder
(`drafts/decoder.h` / `drafts/decoder.cpp`).

## Wire facts

- All multi-byte integers are little-endian.
- All floats are IEEE-754 binary32, little-endian byte order.
- All floats must be finite. NaN and ±Inf are rejected as `InvalidField`.
  This is enforced because NaN comparisons silently evaluate to false and
  would bypass time-window and ordering checks in the engine.
- No alignment padding. Every field is byte-packed.
- v3 is a clean break from v2. New decoders do not parse old blobs.

## Header (19 bytes)

| Offset | Size | Type    | Field            |
| ------ | ---- | ------- | ---------------- |
| 0      | 4    | char[4] | magic            |
| 4      | 1    | u8      | version          |
| 5      | 1    | u8      | flags            |
| 6      | 1    | u8      | layer_count      |
| 7      | 2    | u16     | strip_length     |
| 9      | 2    | u16     | buffer_count     |
| 11     | 2    | u16     | pixel_view_count |
| 13     | 2    | u16     | copy_op_count    |
| 15     | 4    | f32     | duration         |

- `magic` — `"ELEM"` (`0x45 0x4C 0x45 0x4D`).
- `version` — `3`.
- `flags` — bit0 = `requires_sync`. All other bits are reserved and must be
  0. The decoder rejects any blob with unknown bits set (`InvalidField`).
- `layer_count` — number of visual layers.
- `strip_length` — physical LED count this blob targets. Must be in
  `[1, kMaxStripPixels]`. Must equal the active
  `HardwareProfile::strip_length` exactly. The compiler bakes physical
  LED indices into PixelView descriptors, so a mismatch is rejected with
  `StripLengthMismatch`, not clamped. A header `strip_length` of 0 is
  rejected as `InvalidField` directly.
- `buffer_count` — number of HSVA pool buffers.
- `pixel_view_count` — number of PixelView descriptors.
- `copy_op_count` — number of CopyOp records.
- `duration` — total program duration in seconds. Must be finite and > 0.

## Buffer sizes section

Repeats `buffer_count` times. Each entry is the pixel count of one HSVA pool
buffer in pool index order.

| Size | Type | Field |
| ---- | ---- | ----- |
| 2    | u16  | size  |

The backing storage cost of one buffer is `size * sizeof(hsva_t)` =
`size * 16` bytes. The total across all buffers must not exceed
`kMaxPoolBytes`.

## PixelView descriptors section

Repeats `pixel_view_count` times. Each descriptor has a 5-byte fixed prefix
followed by zero, one, or two optional index arrays.

Fixed prefix:

| Size | Type | Field      |
| ---- | ---- | ---------- |
| 2    | u16  | buffer_idx |
| 2    | u16  | size       |
| 1    | u8   | flags      |

Flag bits:

- bit0 — `storage_identity`: `view[i]` maps to `buffer[i]`.
  `storage_indices` is omitted.
- bit1 — `has_physical`: this view has a physical LED mapping and may be used
  as an event `dst` view.
- bit2 — `physical_identity`: physical map is identity (`view[i]` → LED `i`).
  Only meaningful when `has_physical` is set. `physical_indices` is omitted.

Bits 3–7 are reserved and must be 0. The decoder rejects any descriptor
with unknown bits set (`InvalidField`).

When `has_physical && physical_identity`, the implicit `view[i] → LED i`
mapping requires `size <= strip_length`. The decoder rejects descriptors
that violate this with `InvalidField`.

Optional storage indices, present iff `storage_identity == false`:

| Size     | Type      | Field           |
| -------- | --------- | --------------- |
| 2 * size | u16[size] | storage_indices |

Each entry is a slot index into `pool_buffer[buffer_idx]`.

Optional physical indices, present iff `has_physical && !physical_identity`:

| Size     | Type      | Field            |
| -------- | --------- | ---------------- |
| 2 * size | u16[size] | physical_indices |

Each entry is a physical LED index in `[0, strip_length)`.

A view with `has_physical == false && physical_identity == true` is malformed.

## Copy ops section

Repeats `copy_op_count` times. Records must be sorted ascending by `at`.

| Size | Type | Field        |
| ---- | ---- | ------------ |
| 4    | f32  | at           |
| 2    | u16  | src_pixv_idx |
| 2    | u16  | dst_pixv_idx |

Both indices must be in `[0, pixel_view_count)` and never `PIXV_NONE`. Source
and destination views must have equal `size`. `at` must be finite and in
`[0, duration)`.

Equal `at` values are allowed. Same-time copy ops execute **in blob (table)
order**: the engine processes them sequentially in the order they appear
in this section. The compiler is responsible for placing same-time ops in
a topological order that respects data dependencies (a copy op that reads
a view written by another same-time copy op must appear after the writer)
and for rejecting cycles or two same-time ops that write to the same
destination view (ambiguous aliasing).

## Layers section

Repeats `layer_count` times. Each layer is an event count followed by that
many events.

| Size | Type | Field       |
| ---- | ---- | ----------- |
| 2    | u16  | event_count |
| ...  | ...  | events      |

Events on a layer must be sorted ascending by `start` and must not overlap.

## Event records

Each event:

| Size | Type | Field         |
| ---- | ---- | ------------- |
| 1    | u8   | anim_type     |
| 4    | f32  | start         |
| 4    | f32  | duration      |
| 2    | u16  | src_pixv_idx  |
| 2    | u16  | dst_pixv_idx  |
| 2    | u16  | work_pixv_idx |
| 2    | u16  | params_size   |
| N    | u8[] | params        |

- `anim_type` — `AnimType` enum value (see `animation_types.h`).
- `start` — program-relative seconds. Must be finite and `>= 0`.
- `duration` — seconds. Must be finite and `> 0`.
- `start + duration` must be `<= program duration`.
- `src_pixv_idx` / `work_pixv_idx` — `PIXV_NONE` (`0xFFFF`) when absent;
  otherwise must be in `[0, pixel_view_count)`.
- `dst_pixv_idx` — required, must be in `[0, pixel_view_count)`, and the
  referenced view must have `has_physical == true`.
- `params_size` — exact byte length of the params block. The decoder reads
  exactly this many bytes and hands them to the per-animation factory.
- `params` — animation-type-specific bytes. The shape lives in each
  `anim_*.h` header alongside that animation's class.

Per-animation factories must reject NaN, ±Inf, and out-of-range values in
their own params (the "all floats finite" rule binds factories too, not
just the decoder).

A factory may consume **fewer** than `params_size` bytes; trailing bytes
are reserved for forward-compatible param extensions. The decoder hands
the factory an exact slice and does not enforce that every byte is
consumed. Format extensions that change required semantics must bump the
blob version, not extend params silently.

## Caps

The decoder rejects with `OverCap` if any field exceeds its cap. The Python
compiler enforces the same caps at compile time and aborts the build on
violation (see `decoder.md`).

| Constant             | Value      | Bound                                    |
| -------------------- | ---------- | ---------------------------------------- |
| `kMaxLayerCount`     | 32         | header `layer_count`                     |
| `kMaxStripPixels`    | 1000       | header `strip_length`, per-buffer `size` |
| `kMaxBufferCount`    | 256        | header `buffer_count`                    |
| `kMaxPixelViewCount` | 512        | header `pixel_view_count`                |
| `kMaxCopyOpCount`    | 512        | header `copy_op_count`                   |
| `kMaxEventsPerLayer` | 1024       | per-layer `event_count`                  |
| `kMaxParamsBytes`    | 4096       | per-event `params_size`                  |
| `kMaxPoolBytes`      | 100 * 1024 | sum of `buffer_size * 16` over all pool buffers |

`kMaxStripPixels` is an absolute upper bound. Real strips are typically
50–250 LEDs; firmware should size runtime structures from
`HardwareProfile::strip_length`, not from this cap.

`kMaxPoolBytes` is the global HSVA backing storage cap. It bounds worst-case
heap consumption regardless of how the per-section caps combine.

The per-buffer cap (`size <= kMaxStripPixels`) is a first-pass simplifying
policy, not a fundamental constraint. The hard memory limit is
`kMaxPoolBytes`. The per-buffer cap can be raised later if a future compiler
needs to pack multiple concurrently-live preserved regions into one larger
backing buffer.

These constants live in `drafts/blob_limits.h` and are mirrored by the Python
compiler.

## Decoder rejection reasons

| `DecodeError`         | Cause                                                          |
| --------------------- | -------------------------------------------------------------- |
| `BadMagic`            | first 4 bytes are not `"ELEM"`                                 |
| `BadVersion`          | version byte is not 3                                          |
| `Truncated`           | a read would advance past end of buffer                        |
| `TrailingBytes`       | parser finished before end of buffer                           |
| `InvalidField`        | out-of-range index, malformed flags, unsorted records, or constraint violation |
| `OverCap`             | any cap above is exceeded                                      |
| `StripLengthMismatch` | header `strip_length` != active profile strip length           |
| `OutOfMemory`         | any allocation during decode failed                            |

`BadMagic` and `BadVersion` are checked before any allocation. Header count
caps are checked before allocating any section. `kMaxPoolBytes` is checked
after reading the buffer-sizes table, before pool allocation.
