# Blob Format

## What it is

A *blob* is a single self-contained binary file that holds one compiled
animation program: every layer, event, pixel mapping and parameter the
device needs to play a show, packed into a compact byte stream.

It is created by the animation compiler and sent to the device, which
decodes it and plays it back.

## Overall layout

All values are little-endian and byte-packed.

The blob is a fixed sequence of sections, in this order:

    header        20 bytes, fixed
    buffer sizes  one entry per pool buffer
    pixel views   one descriptor per view (variable length each)
    copy ops      one record per scheduled copy
    layers        one block per layer, each holding its events

The header's count fields tell the decoder how many entries each later
section has. The decoder reads exactly the bytes these counts imply; any
leftover bytes at the end are an error (`TrailingBytes`).

## Header (20 bytes, fixed)

    type      field             description
    char[4]   magic             always "ELEM" (0x45 4C 45 4D)
    u8        version           format version, currently 4
    u8        flags             bit0 = requires_sync; other bits reserved (0)
    u8        target_fps        intended frame rate, Hz
    u8        layer_count       number of layers in the layers section
    u16       strip_length      physical LED count this blob targets
    u16       buffer_count      number of entries in the buffer-sizes section
    u16       pixel_view_count  number of descriptors in the pixel-views section
    u16       copy_op_count     number of records in the copy-ops section
    u32       duration          total program length, milliseconds; must be > 0

## Time

Every timeline value (program duration, event start and duration, copy-op
`at`) is a u32 count of whole milliseconds. The compiler rounds each
authored boundary once, half up, and derives an event's duration from its
rounded start and end. Adjacent events and the copy ops that sit on their
shared boundary therefore compare exactly on the device instead of
drifting apart by a float rounding error. Animation parameters that are
times (periods, fades, velocities) stay float seconds; they are never
compared for ordering.

## Buffer sizes

Sizes for the program's pool buffers: the working pixel memory it draws
into, held by [`PixelBufferPool`](abstractions.md#pixelbufferpool).

`buffer_count` entries, in pool-index order:

    type  field  description
    u16   size   pixel count of this HSVA pool buffer

Each buffer backs `size * 16` bytes of pixel storage.

## Pixel views

`pixel_view_count` descriptors. A pixel view is a named slice into a pool
buffer, optionally with a physical LED mapping. Each descriptor is a
5-byte prefix followed by zero, one, or two index arrays whose presence
depends on the flags:

    type        field            description
    u16         buffer_idx       which pool buffer this view reads/writes
    u16         size             number of pixels in the view
    u8          flags            see below
    u16[size]   storage_indices  present only if storage_identity = 0
    u16[size]   physical_indices present only if has_physical = 1 and physical_identity = 0

Flags byte:

    bit0  storage_identity   view[i] maps straight to buffer slot i
    bit1  has_physical       view has a physical LED mapping; can be an event dst
    bit2  physical_identity   physical map is identity: view[i] -> LED i
    3-7   reserved (must be 0)

Rules:

- `storage_indices[i]` is a slot index into `buffer[buffer_idx]`; each
  must be `< buffer_size`. Omitted when `storage_identity` is set.
- `physical_indices[i]` is an LED index in `[0, strip_length)`. Omitted
  when the map is identity, and absent entirely without `has_physical`.
- `has_physical = 0` with `physical_identity = 1` is malformed.
- When `has_physical` and `physical_identity` are both set, `size` must
  be `<= strip_length`.

## Copy ops

`copy_op_count` records, sorted ascending by `at`. A copy op moves pixels
from one view to another at a scheduled time:

    type  field         description
    u32   at            when to run, milliseconds; in [0, duration)
    u16   src_pixv_idx  source view; must be valid, not PIXV_NONE
    u16   dst_pixv_idx  destination view; must be valid, not PIXV_NONE

Both views must have equal `size`. Ops with the same `at` run in blob
order; the compiler emits them in a dependency-respecting topological
order and forbids two same-time ops writing to the same destination.

The engine runs an op after every event ending at `at` has rendered its
endpoint sample (its value at its own end) and before any event starting
at `at` initializes. An op at a source's end therefore captures the
source's final pixels whether or not a frame was rendered anywhere near
that time.

## Layers and events

`layer_count` layers, in order. Each layer starts with its event count,
then that many events:

    type  field        description
    u16   event_count  number of events on this layer

Events on a layer must be sorted ascending by `start` and must not
overlap in time; an event may start exactly where the previous one ends.
Each event:

    type     field          description
    u8       anim_type      AnimType enum (Wave=0, Shift=1, Spark=2, Paint=3)
    u32      start          program-relative start, milliseconds; < program duration
    u32      duration       length, milliseconds; > 0; start+duration <= program duration
    u16      src_pixv_idx   source view, or PIXV_NONE if unused
    u16      dst_pixv_idx   destination view; required, must have has_physical
    u16      work_pixv_idx  scratch view, or PIXV_NONE if unused
    u16      params_size    byte length of the params block that follows
    u8[]     params         animation-specific bytes (params_size long)

Notes:

- `dst_pixv_idx` is mandatory and must reference a view with
  `has_physical` set; that is what reaches real LEDs.
- `params` layout is owned by each animation's `from_blob()` factory
  (see `src/core/animations/*.h`). A factory may read fewer bytes than
  `params_size`; trailing bytes are reserved for forward-compatible
  extensions. Changes that alter meaning must bump the blob version.
- Some animations add post-parse constraints (e.g. Shift requires both
  `src` and `work` views of equal size; a Paint constant array must
  match the destination view's size).

## Limits

Structural caps the decoder enforces before allocating. Defined in
`src/core/blob_limits.h` and mirrored by the compiler:

    constant                value    bounds
    MAX_LAYER_COUNT         32       header layer_count
    MAX_STRIP_PIXELS        300      header strip_length, each buffer size
    MAX_BUFFER_COUNT        256      header buffer_count
    MAX_PIXEL_VIEW_COUNT    512      header pixel_view_count
    MAX_COPY_OP_COUNT       512      header copy_op_count
    MAX_EVENTS_PER_LAYER    1024     per-layer event_count
    MAX_EVENT_PARAMS_BYTES  8192     per-event params_size
    MAX_POOL_BYTES          100 KiB  sum of size*16 over all pool buffers

## Rejection reasons

`decode_program()` returns one `DecodeError` on failure (see
`src/core/blob_reader.h`):

    BadMagic             first 4 bytes are not "ELEM"
    BadVersion           version byte is not 4
    Truncated            a read ran past the end of the buffer
    TrailingBytes        bytes remained after the last section was read
    InvalidField         out-of-range index, bad flags, unsorted/overlapping records, zero duration
    OverCap              a limit above was exceeded
    StripLengthMismatch  header strip_length != active profile strip length
    OutOfMemory          an allocation failed during decode

`BadMagic` and `BadVersion` are checked before any allocation. Count caps
are checked before allocating their section; `MAX_POOL_BYTES` is checked
after reading buffer sizes, before the pool is allocated.
