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

#include "animations/paint.h"
#include "animations/shift.h"
#include "animations/spark.h"
#include "animations/wave.h"

namespace {

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

DecodeError validate_prefix(BlobReader& r)
{
    const uint8_t* magic = r.take(4);
    if (magic == nullptr) return DecodeError::Truncated;
    if (std::memcmp(magic, BLOB_MAGIC, 4) != 0) return DecodeError::BadMagic;

    uint8_t version = 0;
    if (!r.read_u8(version)) return DecodeError::Truncated;
    if (version != BLOB_VERSION) return DecodeError::BadVersion;
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
    // Reserved flag bits must be zero so a future version can introduce a
    // new bit knowing old decoders rejected blobs that set it.
    if ((hdr.flags & ~uint8_t{0x01}) != 0)            return DecodeError::InvalidField;
    if (hdr.strip_length == 0)                         return DecodeError::InvalidField;
    if (hdr.target_fps == 0)                           return DecodeError::InvalidField;

    if (hdr.layer_count      > MAX_LAYER_COUNT)        return DecodeError::OverCap;
    if (hdr.strip_length     > MAX_STRIP_PIXELS)       return DecodeError::OverCap;
    if (hdr.buffer_count     > MAX_BUFFER_COUNT)       return DecodeError::OverCap;
    if (hdr.pixel_view_count > MAX_PIXEL_VIEW_COUNT)   return DecodeError::OverCap;
    if (hdr.copy_op_count    > MAX_COPY_OP_COUNT)      return DecodeError::OverCap;

    if (!std::isfinite(hdr.duration) || hdr.duration <= 0.0f) {
        return DecodeError::InvalidField;
    }
    return DecodeError::Ok;
}

DecodeError parse_buffer_sizes(BlobReader& r, const ParsedHeader& hdr,
                               Program& prog)
{
    if (hdr.buffer_count == 0) {
        return DecodeError::Ok;
    }

    uint16_t* sizes = new (std::nothrow) uint16_t[hdr.buffer_count];
    if (sizes == nullptr) return DecodeError::OutOfMemory;

    size_t total_pixels = 0;
    for (uint16_t i = 0; i < hdr.buffer_count; i++) {
        if (!r.read_u16_le(sizes[i])) {
            delete[] sizes;
            return DecodeError::Truncated;
        }
        if (sizes[i] > MAX_STRIP_PIXELS) {
            delete[] sizes;
            return DecodeError::OverCap;
        }
        total_pixels += sizes[i];
    }
    if (total_pixels * sizeof(hsva_t) > MAX_POOL_BYTES) {
        delete[] sizes;
        return DecodeError::OverCap;
    }

    const bool ok = prog.pixel_buffer_pool.initialize(sizes, hdr.buffer_count);
    delete[] sizes;
    return ok ? DecodeError::Ok : DecodeError::OutOfMemory;
}

DecodeError parse_pixel_views(BlobReader& r, const ParsedHeader& hdr,
                              Program& prog)
{
    if (hdr.pixel_view_count == 0) {
        return DecodeError::Ok;
    }

    PixelViewSpec* specs = new (std::nothrow) PixelViewSpec[hdr.pixel_view_count];
    if (specs == nullptr) return DecodeError::OutOfMemory;

    // Index arrays we own and must free after PixelViews::initialize copies them.
    auto cleanup = [&]() {
        for (uint16_t i = 0; i < hdr.pixel_view_count; i++) {
            delete[] const_cast<uint16_t*>(specs[i].storage_indices);
            delete[] const_cast<uint16_t*>(specs[i].physical_indices);
        }
        delete[] specs;
    };

    for (uint16_t i = 0; i < hdr.pixel_view_count; i++) {
        uint16_t buffer_idx = 0, size = 0;
        uint8_t flags = 0;
        if (!r.read_u16_le(buffer_idx) ||
            !r.read_u16_le(size) ||
            !r.read_u8(flags)) {
            cleanup();
            return DecodeError::Truncated;
        }

        if ((flags & ~uint8_t{0x07}) != 0) { cleanup(); return DecodeError::InvalidField; }
        const bool storage_identity  = (flags & 0x01) != 0;
        const bool has_physical      = (flags & 0x02) != 0;
        const bool physical_identity = (flags & 0x04) != 0;

        if (buffer_idx >= hdr.buffer_count) { cleanup(); return DecodeError::InvalidField; }
        const uint16_t buffer_size = prog.pixel_buffer_pool.buffer_size(buffer_idx);

        if (storage_identity && size > buffer_size)         { cleanup(); return DecodeError::InvalidField; }
        if (!has_physical && physical_identity)              { cleanup(); return DecodeError::InvalidField; }
        if (has_physical && physical_identity && size > hdr.strip_length) {
            cleanup();
            return DecodeError::InvalidField;
        }

        specs[i].buffer_idx           = buffer_idx;
        specs[i].size                 = size;
        specs[i].storage_identity     = storage_identity;
        specs[i].has_physical_mapping = has_physical;
        specs[i].physical_identity    = physical_identity;

        if (!storage_identity) {
            uint16_t* idx = new (std::nothrow) uint16_t[size];
            if (idx == nullptr) { cleanup(); return DecodeError::OutOfMemory; }
            for (uint16_t k = 0; k < size; k++) {
                if (!r.read_u16_le(idx[k])) {
                    delete[] idx;
                    cleanup();
                    return DecodeError::Truncated;
                }
                if (idx[k] >= buffer_size) {
                    delete[] idx;
                    cleanup();
                    return DecodeError::InvalidField;
                }
            }
            specs[i].storage_indices = idx;
        }

        if (has_physical && !physical_identity) {
            uint16_t* idx = new (std::nothrow) uint16_t[size];
            if (idx == nullptr) { cleanup(); return DecodeError::OutOfMemory; }
            for (uint16_t k = 0; k < size; k++) {
                if (!r.read_u16_le(idx[k])) {
                    delete[] idx;
                    cleanup();
                    return DecodeError::Truncated;
                }
                if (idx[k] >= hdr.strip_length) {
                    delete[] idx;
                    cleanup();
                    return DecodeError::InvalidField;
                }
            }
            specs[i].physical_indices = idx;
        }
    }

    const bool ok = prog.pixel_views.initialize(prog.pixel_buffer_pool, specs,
                                                hdr.pixel_view_count);
    cleanup();
    return ok ? DecodeError::Ok : DecodeError::OutOfMemory;
}

DecodeError parse_copy_ops(BlobReader& r, const ParsedHeader& hdr, Program& prog)
{
    if (hdr.copy_op_count == 0) {
        return DecodeError::Ok;
    }

    CopyOp* ops = new (std::nothrow) CopyOp[hdr.copy_op_count];
    if (ops == nullptr) return DecodeError::OutOfMemory;

    float prev_at = 0.0f;
    uint16_t same_at_group_start = 0;

    for (uint16_t i = 0; i < hdr.copy_op_count; i++) {
        if (!r.read_f32_le(ops[i].at) ||
            !r.read_u16_le(ops[i].src_pixv_idx) ||
            !r.read_u16_le(ops[i].dst_pixv_idx)) {
            delete[] ops;
            return DecodeError::Truncated;
        }

        const float    at  = ops[i].at;
        const uint16_t src = ops[i].src_pixv_idx;
        const uint16_t dst = ops[i].dst_pixv_idx;

        if (!std::isfinite(at) || at < 0.0f || at >= hdr.duration) {
            delete[] ops;
            return DecodeError::InvalidField;
        }
        if (i > 0 && at < prev_at) {
            delete[] ops;
            return DecodeError::InvalidField;
        }
        if (src == PIXV_NONE || src >= hdr.pixel_view_count) {
            delete[] ops;
            return DecodeError::InvalidField;
        }
        if (dst == PIXV_NONE || dst >= hdr.pixel_view_count) {
            delete[] ops;
            return DecodeError::InvalidField;
        }
        if (prog.pixel_views.at(src).size() != prog.pixel_views.at(dst).size()) {
            delete[] ops;
            return DecodeError::InvalidField;
        }

        if (i > 0 && at > prev_at) {
            same_at_group_start = i;
        } else if (i > 0) {
            // Same-`at` as the previous op: reject duplicate dst (ambiguous
            // aliasing). Topological order across same-`at` ops is the
            // compiler's job.
            for (uint16_t j = same_at_group_start; j < i; j++) {
                if (ops[j].dst_pixv_idx == dst) {
                    delete[] ops;
                    return DecodeError::InvalidField;
                }
            }
        }
        prev_at = at;
    }

    const bool ok = prog.copy_ops.initialize(ops, hdr.copy_op_count);
    delete[] ops;
    return ok ? DecodeError::Ok : DecodeError::OutOfMemory;
}

DecodeError parse_event(BlobReader& r, const ParsedHeader& hdr,
                        const Program& prog, AnimationEvent& event)
{
    uint8_t  anim_type     = 0;
    float    start         = 0.0f;
    float    duration      = 0.0f;
    uint16_t src_pixv_idx  = PIXV_NONE;
    uint16_t dst_pixv_idx  = PIXV_NONE;
    uint16_t work_pixv_idx = PIXV_NONE;
    uint16_t params_size   = 0;

    if (!r.read_u8(anim_type) ||
        !r.read_f32_le(start) ||
        !r.read_f32_le(duration) ||
        !r.read_u16_le(src_pixv_idx) ||
        !r.read_u16_le(dst_pixv_idx) ||
        !r.read_u16_le(work_pixv_idx) ||
        !r.read_u16_le(params_size)) {
        return DecodeError::Truncated;
    }

    if (!std::isfinite(start) || start < 0.0f)               return DecodeError::InvalidField;
    if (!std::isfinite(duration) || duration <= 0.0f)        return DecodeError::InvalidField;
    if (start + duration > hdr.duration)                     return DecodeError::InvalidField;
    if (params_size > MAX_PARAMS_BYTES)                      return DecodeError::OverCap;

    if (dst_pixv_idx == PIXV_NONE)                           return DecodeError::InvalidField;
    if (dst_pixv_idx >= hdr.pixel_view_count)                return DecodeError::InvalidField;
    if (!prog.pixel_views.at(dst_pixv_idx).has_physical_mapping()) {
        return DecodeError::InvalidField;
    }
    if (src_pixv_idx != PIXV_NONE && src_pixv_idx >= hdr.pixel_view_count) {
        return DecodeError::InvalidField;
    }
    if (work_pixv_idx != PIXV_NONE && work_pixv_idx >= hdr.pixel_view_count) {
        return DecodeError::InvalidField;
    }

    const uint8_t* p = r.take(params_size);
    if (params_size > 0 && p == nullptr) return DecodeError::Truncated;

    const AnimType type = static_cast<AnimType>(anim_type);
    DecodeError perr = DecodeError::Ok;
    Animation* anim = nullptr;
    switch (type) {
        case AnimType::Wave:  anim = Wave::from_blob(p, params_size, &perr);  break;
        case AnimType::Shift: anim = Shift::from_blob(p, params_size, &perr); break;
        case AnimType::Spark: anim = Spark::from_blob(p, params_size, &perr); break;
        case AnimType::Paint: anim = Paint::from_blob(p, params_size, &perr); break;
        default: return DecodeError::InvalidField;
    }
    if (anim == nullptr) return perr;

    // Per-anim post-checks (constraints from_blob can't see).
    if (type == AnimType::Paint) {
        Paint* paint = static_cast<Paint*>(anim);
        const uint8_t k = paint->constant_array_size();
        if (k != 0 && k != prog.pixel_views.at(dst_pixv_idx).size()) {
            delete anim;
            return DecodeError::InvalidField;
        }
    }
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

    event.animation     = anim;
    event.start         = start;
    event.duration      = duration;
    event.src_pixv_idx  = src_pixv_idx;
    event.dst_pixv_idx  = dst_pixv_idx;
    event.work_pixv_idx = work_pixv_idx;
    return DecodeError::Ok;
}

DecodeError parse_layers(BlobReader& r, const ParsedHeader& hdr, Program& prog)
{
    if (hdr.layer_count == 0) return DecodeError::Ok;

    prog.layers = new (std::nothrow) Layer[hdr.layer_count];
    if (prog.layers == nullptr) return DecodeError::OutOfMemory;

    for (uint8_t li = 0; li < hdr.layer_count; li++) {
        uint16_t event_count = 0;
        if (!r.read_u16_le(event_count)) return DecodeError::Truncated;
        if (event_count > MAX_EVENTS_PER_LAYER) return DecodeError::OverCap;
        if (event_count == 0) {
            prog.layers[li].initialize(nullptr, 0);
            continue;
        }

        AnimationEvent* events = new (std::nothrow) AnimationEvent[event_count];
        if (events == nullptr) return DecodeError::OutOfMemory;

        float prev_end = 0.0f;
        for (uint16_t ei = 0; ei < event_count; ei++) {
            const DecodeError err = parse_event(r, hdr, prog, events[ei]);
            if (err != DecodeError::Ok) {
                for (uint16_t j = 0; j < ei; j++) delete events[j].animation;
                delete[] events;
                return err;
            }
            if (events[ei].start < prev_end) {
                for (uint16_t j = 0; j <= ei; j++) delete events[j].animation;
                delete[] events;
                return DecodeError::InvalidField;
            }
            prev_end = events[ei].start + events[ei].duration;
        }

        prog.layers[li].initialize(events, event_count);
    }
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
