#include "decoder.h"

#include <cmath>
#include <cstring>
#include <new>

#include "animation_types.h"
#include "blob_limits.h"
#include "blob_reader.h"
#include "copy_ops.h"
#include "layer.h"
#include "pixel_buffer_pool.h"
#include "pixel_views.h"
#include "program.h"
#include "runtime_constants.h"

// TODO: include each concrete animation header. The decoder is the only
// translation unit that pulls in all of them.
//
// #include "anim_wave.h"
// #include "anim_shift.h"
// #include "anim_spark.h"
// #include "anim_paint.h"

// BlobReader and decode_error_name() are implemented in blob_reader.cpp so
// per-animation factories can link against them without depending on this
// translation unit.

// ---------------------------------------------------------------------------
// decode_program
// ---------------------------------------------------------------------------

namespace {

// Stack-local parsed header. Not a wire struct.
struct ParsedHeader {
    uint8_t  flags;
    uint8_t  target_fps;
    uint8_t  layer_count;
    uint16_t strip_length;
    uint16_t buffer_count;
    uint16_t pixel_view_count;
    uint16_t copy_op_count;
    float    duration;
};

// Validate magic + version with no allocation.
DecodeError validate_prefix(BlobReader& r)
{
    const uint8_t* magic = r.take(4);
    if (magic == nullptr) return DecodeError::Truncated;
    if (std::memcmp(magic, kBlobMagic, 4) != 0) return DecodeError::BadMagic;

    uint8_t version = 0;
    if (!r.read_u8(version)) return DecodeError::Truncated;
    if (version != kBlobVersion) return DecodeError::BadVersion;
    return DecodeError::Ok;
}

DecodeError parse_header(BlobReader& r, ParsedHeader& hdr)
{
    if (!r.read_u8(hdr.flags))                return DecodeError::Truncated;
    if (!r.read_u8(hdr.target_fps))           return DecodeError::Truncated;
    if (!r.read_u8(hdr.layer_count))          return DecodeError::Truncated;
    if (!r.read_u16_le(hdr.strip_length))     return DecodeError::Truncated;
    if (!r.read_u16_le(hdr.buffer_count))     return DecodeError::Truncated;
    if (!r.read_u16_le(hdr.pixel_view_count)) return DecodeError::Truncated;
    if (!r.read_u16_le(hdr.copy_op_count))    return DecodeError::Truncated;
    if (!r.read_f32_le(hdr.duration))         return DecodeError::Truncated;
    return DecodeError::Ok;
}

DecodeError validate_header(const ParsedHeader& hdr)
{
    // Reserved flag bits must be zero so a future v3.x can introduce a new
    // bit knowing that old decoders rejected blobs that set it.
    if ((hdr.flags & ~uint8_t{0x01}) != 0) return DecodeError::InvalidField;

    // strip_length == 0 is rejected here as defense in depth; the caller's
    // HardwareProfile::is_valid() also requires >= 1, but a buggy or
    // detached caller could pass 0 and a 0-length blob would otherwise
    // pass the equality check below.
    if (hdr.strip_length == 0)                     return DecodeError::InvalidField;
    if (hdr.target_fps == 0)                       return DecodeError::InvalidField;

    if (hdr.layer_count      > MAX_LAYER_COUNT)       return DecodeError::OverCap;
    if (hdr.strip_length     > MAX_STRIP_PIXELS)      return DecodeError::OverCap;
    if (hdr.buffer_count     > MAX_BUFFER_COUNT)      return DecodeError::OverCap;
    if (hdr.pixel_view_count > MAX_PIXEL_VIEW_COUNT)  return DecodeError::OverCap;
    if (hdr.copy_op_count    > MAX_COPY_OP_COUNT)     return DecodeError::OverCap;

    // Program duration must be a finite positive number. NaN and ±Inf would
    // silently break time comparisons in Engine and event ordering checks
    // because NaN comparisons are always false.
    if (!std::isfinite(hdr.duration) || hdr.duration <= 0.0f) {
        return DecodeError::InvalidField;
    }
    return DecodeError::Ok;
}

// The section parsers below are sketched as TODO bodies. Each must:
// - read its count-driven section using BlobReader
// - validate every per-element constraint listed in blob_format.md
// - allocate into the appropriate runtime container
// - return DecodeError::Ok on success
//
// On the first failing return, decode_program calls free_program() to tear
// down whatever was already built.

DecodeError parse_buffer_sizes(BlobReader& r, const ParsedHeader& hdr,
                               Program& prog)
{
    // TODO:
    // - allocate a temporary uint16_t[hdr.buffer_count]
    // - for each buffer i: read u16 size; reject OverCap if > MAX_STRIP_PIXELS
    // - sum total_pixels; reject OverCap if total_pixels * sizeof(hsva_t)
    //   > MAX_POOL_BYTES
    // - prog.pixel_buffer_pool.initialize(sizes, hdr.buffer_count)
    //   → OutOfMemory if it returns false
    // - free the temporary
    (void)r; (void)hdr; (void)prog;
    return DecodeError::Ok;
}

DecodeError parse_pixel_views(BlobReader& r, const ParsedHeader& hdr,
                              Program& prog)
{
    // TODO:
    // - allocate a temporary PixelViewSpec[hdr.pixel_view_count]
    // - for each spec i:
    //   - read buffer_idx u16, size u16, flags u8
    //   - reject InvalidField if (flags & ~0x07) != 0 (unknown bits set)
    //   - decode bit0 storage_identity, bit1 has_physical, bit2 physical_identity
    //   - reject InvalidField if buffer_idx >= hdr.buffer_count
    //   - reject InvalidField if storage_identity && size >
    //     pool.buffer_size(buffer_idx)
    //   - reject InvalidField if !has_physical && physical_identity
    //   - reject InvalidField if has_physical && physical_identity &&
    //     size > hdr.strip_length
    //     (the implicit map view[i] -> LED i would otherwise route past
    //      the end of the strip)
    //   - if !storage_identity:
    //     - allocate uint16_t[size] (per-spec temporary)
    //     - read each u16 via read_u16_le; reject InvalidField if any
    //       index >= pool.buffer_size(buffer_idx)
    //     - point spec.storage_indices at the temporary
    //   - if has_physical && !physical_identity:
    //     - allocate uint16_t[size] (per-spec temporary)
    //     - read each u16 via read_u16_le; reject InvalidField if any
    //       index >= hdr.strip_length
    //     - point spec.physical_indices at the temporary
    // - prog.pixel_views.initialize(prog.pixel_buffer_pool, specs,
    //                               hdr.pixel_view_count)
    //   → OutOfMemory if it returns false
    //   PixelViews copies the indices into per-view owned storage; the
    //   per-spec temporaries can be freed after initialize() returns.
    // - free per-spec temporary index arrays and the specs array
    (void)r; (void)hdr; (void)prog;
    return DecodeError::Ok;
}

DecodeError parse_copy_ops(BlobReader& r, const ParsedHeader& hdr,
                           Program& prog)
{
    // TODO:
    // - allocate a temporary CopyOp[hdr.copy_op_count]
    // - prev_at = 0
    // - same_at_group_start = 0  // index of first op in the current same-at run
    // - for each op i:
    //   - read at f32, src_pixv_idx u16, dst_pixv_idx u16
    //   - reject InvalidField if !std::isfinite(at) || at < 0.0f ||
    //     at >= hdr.duration
    //   - reject InvalidField if at < prev_at (must be sorted ascending)
    //   - reject InvalidField if either index == PIXV_NONE or
    //     >= hdr.pixel_view_count
    //   - reject InvalidField if prog.pixel_views.at(src).size() !=
    //     prog.pixel_views.at(dst).size()
    //   - if at > prev_at: same_at_group_start = i
    //     else: for j in [same_at_group_start, i):
    //             reject InvalidField if ops[j].dst_pixv_idx == dst_pixv_idx
    //             (two same-at ops writing the same destination is ambiguous
    //              aliasing — compiler should never emit this; the decoder
    //              rejects it as a cheap defense in depth)
    //   - prev_at = at
    // - prog.copy_ops.initialize(ops, hdr.copy_op_count)
    //   → OutOfMemory if it returns false
    // - free the temporary
    //
    // The decoder does NOT verify topological ordering of same-at copy ops
    // by data dependency. That check is compiler-side; the decoder trusts
    // the compiler-emitted blob order for same-at execution.
    (void)r; (void)hdr; (void)prog;
    return DecodeError::Ok;
}

DecodeError parse_event(BlobReader& r, const ParsedHeader& hdr,
                        const Program& prog, AnimationEvent& event)
{
    // TODO:
    // - read anim_type u8, start f32, duration f32, src/dst/work u16,
    //   params_size u16
    // - reject InvalidField if !std::isfinite(start) || start < 0.0f
    // - reject InvalidField if !std::isfinite(duration) || duration <= 0.0f
    // - reject InvalidField if start + duration > hdr.duration
    // - reject OverCap if params_size > MAX_PARAMS_BYTES
    // - reject InvalidField if dst_pixv_idx == PIXV_NONE or
    //   >= hdr.pixel_view_count
    // - reject InvalidField if !prog.pixel_views.at(dst).has_physical_mapping()
    // - reject InvalidField if src_pixv_idx != PIXV_NONE and
    //   src_pixv_idx >= hdr.pixel_view_count
    // - reject InvalidField if work_pixv_idx != PIXV_NONE and
    //   work_pixv_idx >= hdr.pixel_view_count
    // - p = r.take(params_size);
    //   reject Truncated if params_size > 0 && p == nullptr
    //   (take(0) returns nullptr by convention; the factory's local
    //    BlobReader handles a nullptr+0 input cleanly.)
    // - DecodeError perr = DecodeError::Ok;
    // - dispatch on AnimType (each from_blob writes perr on failure):
    //     case AnimType::Wave:  event.animation = AnimWave::from_blob(p, params_size, &perr); break;
    //     case AnimType::Shift: event.animation = AnimShift::from_blob(p, params_size, &perr); break;
    //     case AnimType::Spark: event.animation = AnimSpark::from_blob(p, params_size, &perr); break;
    //     case AnimType::Paint: event.animation = AnimPaint::from_blob(p, params_size, &perr); break;
    //     default: return DecodeError::InvalidField;
    // - if event.animation == nullptr return perr  // InvalidField or OutOfMemory
    // - per-anim post-checks against the resolved event:
    //     AnimType::Paint constant mode -- the constant array length must
    //     match the dst view size. AnimPaint cannot self-validate (no view
    //     access at construction):
    //       AnimPaint* paint = static_cast<AnimPaint*>(event.animation);
    //       const uint8_t k = paint->constant_array_size();
    //       if (k != 0 && k != prog.pixel_views.at(dst_pixv_idx).size()) {
    //           delete event.animation; event.animation = nullptr;
    //           return DecodeError::InvalidField;
    //       }
    //     AnimType::Shift -- the event must carry src_pixv_idx != PIXV_NONE.
    //     Shift snapshots from src in initialize(); without a source view it
    //     would shift whatever happened to be in work (zeros after reset, or
    //     stale data otherwise) -- not a meaningful render:
    //       if (src_pixv_idx == PIXV_NONE) {
    //           delete event.animation; event.animation = nullptr;
    //           return DecodeError::InvalidField;
    //       }
    // - assign event.start, event.duration, event.{src,dst,work}_pixv_idx
    (void)r; (void)hdr; (void)prog; (void)event;
    return DecodeError::Ok;
}

DecodeError parse_layers(BlobReader& r, const ParsedHeader& hdr,
                         Program& prog)
{
    // TODO:
    // - allocate prog.layers as Layer[hdr.layer_count]
    // - for each layer li:
    //   - read u16 event_count
    //   - reject OverCap if event_count > MAX_EVENTS_PER_LAYER
    //   - allocate AnimationEvent[event_count]
    //   - prev_end = 0
    //   - for each event ei:
    //     - parse_event(r, hdr, prog, events[ei])
    //       → on failure delete partially-built events[0..ei] (their
    //         animations) and the events array, then propagate
    //     - reject InvalidField if events[ei].start < prev_end (sorted +
    //       non-overlapping)
    //     - prev_end = events[ei].start + events[ei].duration
    //   - prog.layers[li].initialize(events, event_count)
    (void)r; (void)hdr; (void)prog;
    return DecodeError::Ok;
}

}  // namespace

Program* decode_program(
    const uint8_t* blob,
    size_t blob_len,
    uint16_t profile_strip_length,
    DecodeError* err_out
)
{
    auto report = [&](DecodeError e) -> Program* {
        if (err_out != nullptr) *err_out = e;
        return nullptr;
    };

    // A zero profile_strip_length is a caller bug regardless of blob content.
    // Report it as InvalidField so the framing is "your profile is wrong",
    // not "the blob doesn't match" (which would be misleading).
    if (profile_strip_length == 0) return report(DecodeError::InvalidField);

    BlobReader r(blob, blob_len);

    DecodeError err = validate_prefix(r);
    if (err != DecodeError::Ok) return report(err);

    ParsedHeader hdr{};
    err = parse_header(r, hdr);
    if (err != DecodeError::Ok) return report(err);

    err = validate_header(hdr);
    if (err != DecodeError::Ok) return report(err);

    if (hdr.strip_length != profile_strip_length) {
        return report(DecodeError::StripLengthMismatch);
    }

    Program* prog = new (std::nothrow) Program();
    if (prog == nullptr) return report(DecodeError::OutOfMemory);

    prog->duration      = hdr.duration;
    prog->target_fps    = hdr.target_fps;
    prog->requires_sync = (hdr.flags & 0x01) != 0;
    prog->layer_count   = hdr.layer_count;

    err = parse_buffer_sizes(r, hdr, *prog);
    if (err != DecodeError::Ok) { free_program(prog); return report(err); }

    err = parse_pixel_views(r, hdr, *prog);
    if (err != DecodeError::Ok) { free_program(prog); return report(err); }

    err = parse_copy_ops(r, hdr, *prog);
    if (err != DecodeError::Ok) { free_program(prog); return report(err); }

    err = parse_layers(r, hdr, *prog);
    if (err != DecodeError::Ok) { free_program(prog); return report(err); }

    if (!r.done()) {
        free_program(prog);
        return report(DecodeError::TrailingBytes);
    }

    if (err_out != nullptr) *err_out = DecodeError::Ok;
    return prog;
}

// free_program() lives in program.cpp. The teardown chain:
//
//   delete prog
//     → Program::~Program() releases prog->layers
//       → each Layer::~Layer() calls Layer::reset()
//         → each event's Animation* is deleted
//         → the events array is deleted
//     → embedded PixelBufferPool/PixelViews/CopyOps destructors fire
