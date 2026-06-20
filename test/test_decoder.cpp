#include <catch2/catch_test_macros.hpp>

#include <cmath>
#include <cstdint>
#include <cstring>
#include <vector>

#include "../src/animation_types.h"
#include "../src/animations/paint.h"
#include "../src/blob_limits.h"
#include "../src/blob_reader.h"
#include "../src/colors.h"
#include "../src/decoder.h"
#include "../src/program.h"
#include "../src/runtime_constants.h"

namespace {

// ---------------------------------------------------------------------------
// Blob byte-builder helpers
// ---------------------------------------------------------------------------

void put_u8(std::vector<uint8_t>& b, uint8_t v) { b.push_back(v); }
void put_u16(std::vector<uint8_t>& b, uint16_t v) { b.push_back(v & 0xFF); b.push_back((v >> 8) & 0xFF); }
void put_f32(std::vector<uint8_t>& b, float f) {
    uint8_t bytes[4]; std::memcpy(bytes, &f, 4);
    b.insert(b.end(), bytes, bytes + 4);
}

// Header builder. Caller fills the per-section data afterwards.
struct HeaderBytes {
    uint8_t  flags            = 0;
    uint8_t  target_fps       = 50;
    uint8_t  layer_count      = 0;
    uint16_t strip_length     = 4;
    uint16_t buffer_count     = 0;
    uint16_t pixel_view_count = 0;
    uint16_t copy_op_count    = 0;
    float    duration         = 1.0f;
};

// Append magic + version + header to `b`.
void append_prefix_and_header(std::vector<uint8_t>& b, const HeaderBytes& h) {
    b.insert(b.end(), { 'E', 'L', 'E', 'M' });
    put_u8(b, 3);                 // BLOB_VERSION
    put_u8(b, h.flags);
    put_u8(b, h.target_fps);
    put_u8(b, h.layer_count);
    put_u16(b, h.strip_length);
    put_u16(b, h.buffer_count);
    put_u16(b, h.pixel_view_count);
    put_u16(b, h.copy_op_count);
    put_f32(b, h.duration);
}

// Append a buffer-size table.
void append_buffer_sizes(std::vector<uint8_t>& b, const std::vector<uint16_t>& sizes) {
    for (uint16_t s : sizes) put_u16(b, s);
}

// PixelView spec packed as u16 buffer_idx, u16 size, u8 flags, then optional
// storage indices, then optional physical indices.
struct ViewBytes {
    uint16_t buffer_idx;
    uint16_t size;
    bool storage_identity = true;
    bool has_physical = false;
    bool physical_identity = false;
    std::vector<uint16_t> storage_indices;   // ignored when storage_identity
    std::vector<uint16_t> physical_indices;  // ignored unless has_physical && !physical_identity
};

void append_pixel_view(std::vector<uint8_t>& b, const ViewBytes& v) {
    put_u16(b, v.buffer_idx);
    put_u16(b, v.size);
    uint8_t flags = 0;
    if (v.storage_identity)  flags |= 0x01;
    if (v.has_physical)      flags |= 0x02;
    if (v.physical_identity) flags |= 0x04;
    put_u8(b, flags);
    if (!v.storage_identity) {
        for (uint16_t idx : v.storage_indices) put_u16(b, idx);
    }
    if (v.has_physical && !v.physical_identity) {
        for (uint16_t idx : v.physical_indices) put_u16(b, idx);
    }
}

struct CopyOpBytes {
    float at;
    uint16_t src;
    uint16_t dst;
};

void append_copy_op(std::vector<uint8_t>& b, const CopyOpBytes& op) {
    put_f32(b, op.at);
    put_u16(b, op.src);
    put_u16(b, op.dst);
}

// Event byte layout: u8 anim_type, f32 start, f32 duration, u16 src, u16 dst,
// u16 work, u16 params_size, then params bytes.
struct EventBytes {
    uint8_t anim_type;
    float start;
    float duration;
    uint16_t src = PIXV_NONE;
    uint16_t dst;
    uint16_t work = PIXV_NONE;
    std::vector<uint8_t> params;
};

void append_event(std::vector<uint8_t>& b, const EventBytes& e) {
    put_u8(b, e.anim_type);
    put_f32(b, e.start);
    put_f32(b, e.duration);
    put_u16(b, e.src);
    put_u16(b, e.dst);
    put_u16(b, e.work);
    put_u16(b, static_cast<uint16_t>(e.params.size()));
    b.insert(b.end(), e.params.begin(), e.params.end());
}

// Wave param packing -- mirrors Wave::from_blob layout.
std::vector<uint8_t> wave_params(uint8_t channel, float h, float s, float v,
                                  float min_val, float max_val,
                                  float period, float phase0, float pixel_step) {
    std::vector<uint8_t> p;
    put_u8(p, channel);
    put_f32(p, h); put_f32(p, s); put_f32(p, v);
    put_f32(p, min_val); put_f32(p, max_val);
    put_f32(p, period); put_f32(p, phase0); put_f32(p, pixel_step);
    return p;
}

std::vector<uint8_t> spark_params(float h, float s, float v, float fade) {
    std::vector<uint8_t> p;
    put_f32(p, h); put_f32(p, s); put_f32(p, v); put_f32(p, fade);
    return p;
}

std::vector<uint8_t> paint_solid_params(float h, float s, float v, float a) {
    std::vector<uint8_t> p;
    put_u8(p, 0);
    put_f32(p, h); put_f32(p, s); put_f32(p, v); put_f32(p, a);
    return p;
}

std::vector<uint8_t> paint_constant_params(const std::vector<hsva_t>& pixels) {
    std::vector<uint8_t> p;
    put_u8(p, 1);
    put_u16(p, static_cast<uint16_t>(pixels.size()));
    for (const auto& px : pixels) {
        put_f32(p, px.h); put_f32(p, px.s); put_f32(p, px.v); put_f32(p, px.a);
    }
    return p;
}

std::vector<uint8_t> shift_params(uint8_t direction, float velocity, uint8_t circular,
                                   float fh, float fs, float fv, float fa) {
    std::vector<uint8_t> p;
    put_u8(p, direction);
    put_f32(p, velocity);
    put_u8(p, circular);
    put_f32(p, fh); put_f32(p, fs); put_f32(p, fv); put_f32(p, fa);
    return p;
}

// Builds a minimal valid blob: 1 buffer of 4 pixels, 1 view (identity storage,
// identity physical), 1 layer with 1 wave event spanning [0, 1).
std::vector<uint8_t> build_minimal_valid_blob() {
    std::vector<uint8_t> b;
    HeaderBytes h;
    h.layer_count = 1;
    h.buffer_count = 1;
    h.pixel_view_count = 1;
    h.duration = 1.0f;
    append_prefix_and_header(b, h);
    append_buffer_sizes(b, { 4 });

    ViewBytes v;
    v.buffer_idx = 0; v.size = 4;
    v.storage_identity = true;
    v.has_physical = true;
    v.physical_identity = true;
    append_pixel_view(b, v);

    // 1 event count for layer 0
    put_u16(b, 1);
    EventBytes e;
    e.anim_type = static_cast<uint8_t>(AnimType::Wave);
    e.start = 0.0f; e.duration = 1.0f; e.dst = 0;
    e.params = wave_params(2, 0, 0, 0, 0, 1, 1.0f, 0, 0);
    append_event(b, e);
    return b;
}

DecodeError run(const std::vector<uint8_t>& bytes, uint16_t profile_strip_length = 4) {
    DecodeError err = DecodeError::Ok;
    Program* p = decode_program(bytes.data(), bytes.size(), profile_strip_length, &err);
    if (p != nullptr) free_program(p);
    return err;
}

}  // namespace

// ---------------------------------------------------------------------------
// Magic / version
// ---------------------------------------------------------------------------

TEST_CASE("decode_program: bad magic", "[decoder]") {
    auto bytes = build_minimal_valid_blob();
    bytes[0] = 'X';
    CHECK(run(bytes) == DecodeError::BadMagic);
}

TEST_CASE("decode_program: bad version", "[decoder]") {
    auto bytes = build_minimal_valid_blob();
    bytes[4] = 99;   // version byte (right after the 4-byte magic)
    CHECK(run(bytes) == DecodeError::BadVersion);
}

TEST_CASE("decode_program: truncated before magic", "[decoder]") {
    std::vector<uint8_t> bytes = { 'E', 'L', 'E' };
    CHECK(run(bytes) == DecodeError::Truncated);
}

// ---------------------------------------------------------------------------
// Caller bug
// ---------------------------------------------------------------------------

TEST_CASE("decode_program: profile_strip_length == 0 is rejected", "[decoder]") {
    auto bytes = build_minimal_valid_blob();
    CHECK(run(bytes, /*profile*/ 0) == DecodeError::InvalidField);
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

TEST_CASE("decode_program: reserved flag bits rejected", "[decoder]") {
    auto bytes = build_minimal_valid_blob();
    bytes[5] = 0x02;   // flags byte: bit 0 is requires_sync; bit 1 reserved
    CHECK(run(bytes) == DecodeError::InvalidField);
}

TEST_CASE("decode_program: target_fps == 0 rejected", "[decoder]") {
    auto bytes = build_minimal_valid_blob();
    bytes[6] = 0;   // target_fps
    CHECK(run(bytes) == DecodeError::InvalidField);
}

TEST_CASE("decode_program: strip_length 0 rejected", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.strip_length = 0;
    append_prefix_and_header(b, h);
    CHECK(run(b, /*profile*/ 4) == DecodeError::InvalidField);
}

TEST_CASE("decode_program: strip_length over MAX_STRIP_PIXELS rejected", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.strip_length = MAX_STRIP_PIXELS + 1;
    append_prefix_and_header(b, h);
    CHECK(run(b, MAX_STRIP_PIXELS + 1) == DecodeError::OverCap);
}

TEST_CASE("decode_program: strip_length mismatch rejected", "[decoder]") {
    auto bytes = build_minimal_valid_blob();
    CHECK(run(bytes, /*profile*/ 8) == DecodeError::StripLengthMismatch);
}

TEST_CASE("decode_program: duration NaN rejected", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.duration = std::nanf("");
    append_prefix_and_header(b, h);
    CHECK(run(b) == DecodeError::InvalidField);
}

TEST_CASE("decode_program: duration <= 0 rejected", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.duration = 0.0f;
    append_prefix_and_header(b, h);
    CHECK(run(b) == DecodeError::InvalidField);
}

TEST_CASE("decode_program: layer_count over MAX_LAYER_COUNT rejected", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.layer_count = MAX_LAYER_COUNT + 1;
    append_prefix_and_header(b, h);
    CHECK(run(b) == DecodeError::OverCap);
}

// ---------------------------------------------------------------------------
// Buffer sizes
// ---------------------------------------------------------------------------

TEST_CASE("decode_program: buffer size over MAX_STRIP_PIXELS rejected", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.buffer_count = 1;
    append_prefix_and_header(b, h);
    append_buffer_sizes(b, { static_cast<uint16_t>(MAX_STRIP_PIXELS + 1) });
    CHECK(run(b) == DecodeError::OverCap);
}

TEST_CASE("decode_program: total pool bytes over MAX_POOL_BYTES rejected", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.buffer_count = 8;
    append_prefix_and_header(b, h);
    // Each buffer 1000 pixels * 16 bytes = 16000; 8 of them = 128000 > 100KB.
    append_buffer_sizes(b, { 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000 });
    CHECK(run(b) == DecodeError::OverCap);
}

TEST_CASE("decode_program: truncated buffer-size table", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.buffer_count = 2;
    append_prefix_and_header(b, h);
    put_u16(b, 4);   // first size
    // missing the second
    CHECK(run(b) == DecodeError::Truncated);
}

// ---------------------------------------------------------------------------
// Pixel views
// ---------------------------------------------------------------------------

TEST_CASE("decode_program: pixel view buffer_idx out of range", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.buffer_count = 1; h.pixel_view_count = 1;
    append_prefix_and_header(b, h);
    append_buffer_sizes(b, { 4 });
    ViewBytes v;
    v.buffer_idx = 5;   // out of range (only 1 buffer)
    v.size = 4; v.storage_identity = true;
    v.has_physical = true; v.physical_identity = true;
    append_pixel_view(b, v);
    CHECK(run(b) == DecodeError::InvalidField);
}

TEST_CASE("decode_program: pixel view unknown flag bits rejected", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.buffer_count = 1; h.pixel_view_count = 1;
    append_prefix_and_header(b, h);
    append_buffer_sizes(b, { 4 });
    put_u16(b, 0); put_u16(b, 4); put_u8(b, 0x80);   // bit 7 not defined
    CHECK(run(b) == DecodeError::InvalidField);
}

TEST_CASE("decode_program: storage-identity view larger than buffer rejected", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.buffer_count = 1; h.pixel_view_count = 1;
    append_prefix_and_header(b, h);
    append_buffer_sizes(b, { 4 });
    ViewBytes v;
    v.buffer_idx = 0; v.size = 8;   // > buffer size 4
    v.storage_identity = true;
    v.has_physical = true; v.physical_identity = true;
    append_pixel_view(b, v);
    CHECK(run(b) == DecodeError::InvalidField);
}

TEST_CASE("decode_program: physical_identity without has_physical rejected", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.buffer_count = 1; h.pixel_view_count = 1;
    append_prefix_and_header(b, h);
    append_buffer_sizes(b, { 4 });
    put_u16(b, 0); put_u16(b, 4); put_u8(b, 0x05);   // storage_identity + physical_identity
    CHECK(run(b) == DecodeError::InvalidField);
}

TEST_CASE("decode_program: storage_indices entry out of range", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.buffer_count = 1; h.pixel_view_count = 1;
    append_prefix_and_header(b, h);
    append_buffer_sizes(b, { 4 });
    ViewBytes v;
    v.buffer_idx = 0; v.size = 2;
    v.storage_identity = false;
    v.has_physical = true; v.physical_identity = true;
    v.storage_indices = { 0, 99 };   // 99 > buffer size
    append_pixel_view(b, v);
    CHECK(run(b) == DecodeError::InvalidField);
}

TEST_CASE("decode_program: physical_indices entry out of range", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.buffer_count = 1; h.pixel_view_count = 1; h.strip_length = 4;
    append_prefix_and_header(b, h);
    append_buffer_sizes(b, { 4 });
    ViewBytes v;
    v.buffer_idx = 0; v.size = 2;
    v.storage_identity = true;
    v.has_physical = true; v.physical_identity = false;
    v.physical_indices = { 0, 99 };   // 99 > strip_length
    append_pixel_view(b, v);
    CHECK(run(b) == DecodeError::InvalidField);
}

// ---------------------------------------------------------------------------
// Copy ops
// ---------------------------------------------------------------------------

TEST_CASE("decode_program: copy op at NaN rejected", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.buffer_count = 1; h.pixel_view_count = 2; h.copy_op_count = 1;
    append_prefix_and_header(b, h);
    append_buffer_sizes(b, { 4 });
    for (int i = 0; i < 2; i++) {
        ViewBytes v; v.buffer_idx = 0; v.size = 4;
        v.storage_identity = true;
        v.has_physical = (i == 0); v.physical_identity = (i == 0);
        append_pixel_view(b, v);
    }
    append_copy_op(b, { std::nanf(""), 1, 0 });
    CHECK(run(b) == DecodeError::InvalidField);
}

TEST_CASE("decode_program: copy op out-of-order at rejected", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.buffer_count = 1; h.pixel_view_count = 2; h.copy_op_count = 2;
    append_prefix_and_header(b, h);
    append_buffer_sizes(b, { 4 });
    for (int i = 0; i < 2; i++) {
        ViewBytes v; v.buffer_idx = 0; v.size = 4;
        v.storage_identity = true;
        v.has_physical = (i == 0); v.physical_identity = (i == 0);
        append_pixel_view(b, v);
    }
    append_copy_op(b, { 0.5f, 1, 0 });
    append_copy_op(b, { 0.2f, 1, 0 });   // earlier than previous
    CHECK(run(b) == DecodeError::InvalidField);
}

TEST_CASE("decode_program: copy op src/dst size mismatch rejected", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.buffer_count = 2; h.pixel_view_count = 2; h.copy_op_count = 1;
    append_prefix_and_header(b, h);
    append_buffer_sizes(b, { 4, 8 });
    ViewBytes v0; v0.buffer_idx = 0; v0.size = 4;
    v0.storage_identity = true;
    v0.has_physical = true; v0.physical_identity = true;
    append_pixel_view(b, v0);
    ViewBytes v1; v1.buffer_idx = 1; v1.size = 8;
    v1.storage_identity = true;
    v1.has_physical = false; v1.physical_identity = false;
    append_pixel_view(b, v1);
    append_copy_op(b, { 0.5f, 1, 0 });   // size 8 -> size 4
    CHECK(run(b) == DecodeError::InvalidField);
}

TEST_CASE("decode_program: copy op src == PIXV_NONE rejected", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.buffer_count = 1; h.pixel_view_count = 1; h.copy_op_count = 1;
    append_prefix_and_header(b, h);
    append_buffer_sizes(b, { 4 });
    ViewBytes v; v.buffer_idx = 0; v.size = 4;
    v.storage_identity = true;
    v.has_physical = true; v.physical_identity = true;
    append_pixel_view(b, v);
    append_copy_op(b, { 0.5f, PIXV_NONE, 0 });
    CHECK(run(b) == DecodeError::InvalidField);
}

TEST_CASE("decode_program: same-`at` copy ops with duplicate dst rejected", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.buffer_count = 1; h.pixel_view_count = 3; h.copy_op_count = 2;
    append_prefix_and_header(b, h);
    append_buffer_sizes(b, { 4 });
    for (int i = 0; i < 3; i++) {
        ViewBytes v; v.buffer_idx = 0; v.size = 4;
        v.storage_identity = true;
        v.has_physical = (i == 0); v.physical_identity = (i == 0);
        append_pixel_view(b, v);
    }
    append_copy_op(b, { 0.5f, 1, 0 });
    append_copy_op(b, { 0.5f, 2, 0 });   // same at, same dst
    CHECK(run(b) == DecodeError::InvalidField);
}

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------

TEST_CASE("decode_program: event start + duration > program duration rejected", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.buffer_count = 1; h.pixel_view_count = 1; h.layer_count = 1;
    h.duration = 1.0f;
    append_prefix_and_header(b, h);
    append_buffer_sizes(b, { 4 });
    ViewBytes v; v.buffer_idx = 0; v.size = 4;
    v.storage_identity = true;
    v.has_physical = true; v.physical_identity = true;
    append_pixel_view(b, v);
    put_u16(b, 1);
    EventBytes e; e.anim_type = static_cast<uint8_t>(AnimType::Wave);
    e.start = 0.5f; e.duration = 1.0f;   // 1.5 > 1.0
    e.dst = 0;
    e.params = wave_params(2, 0, 0, 0, 0, 1, 1.0f, 0, 0);
    append_event(b, e);
    CHECK(run(b) == DecodeError::InvalidField);
}

TEST_CASE("decode_program: event dst view without physical mapping rejected", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.buffer_count = 1; h.pixel_view_count = 1; h.layer_count = 1;
    h.duration = 1.0f;
    append_prefix_and_header(b, h);
    append_buffer_sizes(b, { 4 });
    ViewBytes v; v.buffer_idx = 0; v.size = 4;
    v.storage_identity = true;
    v.has_physical = false;   // NO physical mapping
    append_pixel_view(b, v);
    put_u16(b, 1);
    EventBytes e; e.anim_type = static_cast<uint8_t>(AnimType::Wave);
    e.start = 0.0f; e.duration = 1.0f; e.dst = 0;
    e.params = wave_params(2, 0, 0, 0, 0, 1, 1.0f, 0, 0);
    append_event(b, e);
    CHECK(run(b) == DecodeError::InvalidField);
}

TEST_CASE("decode_program: unknown anim type rejected", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.buffer_count = 1; h.pixel_view_count = 1; h.layer_count = 1;
    append_prefix_and_header(b, h);
    append_buffer_sizes(b, { 4 });
    ViewBytes v; v.buffer_idx = 0; v.size = 4;
    v.storage_identity = true;
    v.has_physical = true; v.physical_identity = true;
    append_pixel_view(b, v);
    put_u16(b, 1);
    EventBytes e; e.anim_type = 99; e.start = 0.0f; e.duration = 1.0f; e.dst = 0;
    append_event(b, e);
    CHECK(run(b) == DecodeError::InvalidField);
}

TEST_CASE("decode_program: paint constant size mismatch rejected", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.buffer_count = 1; h.pixel_view_count = 1; h.layer_count = 1;
    append_prefix_and_header(b, h);
    append_buffer_sizes(b, { 4 });
    ViewBytes v; v.buffer_idx = 0; v.size = 4;
    v.storage_identity = true;
    v.has_physical = true; v.physical_identity = true;
    append_pixel_view(b, v);
    put_u16(b, 1);
    EventBytes e; e.anim_type = static_cast<uint8_t>(AnimType::Paint);
    e.start = 0.0f; e.duration = 1.0f; e.dst = 0;
    // Only 2 pixels but dst view is 4 -- decoder must reject.
    e.params = paint_constant_params({ hsva_t(0,1,1,1), hsva_t(0,1,1,1) });
    append_event(b, e);
    CHECK(run(b) == DecodeError::InvalidField);
}

TEST_CASE("decode_program: shift event without source rejected", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.buffer_count = 1; h.pixel_view_count = 1; h.layer_count = 1;
    append_prefix_and_header(b, h);
    append_buffer_sizes(b, { 4 });
    ViewBytes v; v.buffer_idx = 0; v.size = 4;
    v.storage_identity = true;
    v.has_physical = true; v.physical_identity = true;
    append_pixel_view(b, v);
    put_u16(b, 1);
    EventBytes e; e.anim_type = static_cast<uint8_t>(AnimType::Shift);
    e.start = 0.0f; e.duration = 1.0f; e.dst = 0; e.src = PIXV_NONE;
    e.params = shift_params(1, 1.0f, 1, 0, 0, 0, 0);
    append_event(b, e);
    CHECK(run(b) == DecodeError::InvalidField);
}

TEST_CASE("decode_program: shift event without work view rejected", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.buffer_count = 1; h.pixel_view_count = 2; h.layer_count = 1;
    append_prefix_and_header(b, h);
    append_buffer_sizes(b, { 4 });
    ViewBytes v0; v0.buffer_idx = 0; v0.size = 4;
    v0.storage_identity = true;
    v0.has_physical = true; v0.physical_identity = true;
    append_pixel_view(b, v0);
    ViewBytes v1; v1.buffer_idx = 0; v1.size = 4;
    v1.storage_identity = true;
    v1.has_physical = false;
    append_pixel_view(b, v1);
    put_u16(b, 1);
    EventBytes e; e.anim_type = static_cast<uint8_t>(AnimType::Shift);
    e.start = 0.0f; e.duration = 1.0f;
    e.dst = 0; e.src = 1; e.work = PIXV_NONE;
    e.params = shift_params(1, 1.0f, 1, 0, 0, 0, 0);
    append_event(b, e);
    CHECK(run(b) == DecodeError::InvalidField);
}

TEST_CASE("decode_program: shift event with zero-size work view rejected", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.buffer_count = 3; h.pixel_view_count = 3; h.layer_count = 1;
    append_prefix_and_header(b, h);
    append_buffer_sizes(b, { 4, 0, 0 });
    ViewBytes v0; v0.buffer_idx = 0; v0.size = 4;
    v0.storage_identity = true;
    v0.has_physical = true; v0.physical_identity = true;
    append_pixel_view(b, v0);
    ViewBytes v1; v1.buffer_idx = 1; v1.size = 0;
    v1.storage_identity = true;
    v1.has_physical = false;
    append_pixel_view(b, v1);
    ViewBytes v2; v2.buffer_idx = 2; v2.size = 0;
    v2.storage_identity = true;
    v2.has_physical = false;
    append_pixel_view(b, v2);
    put_u16(b, 1);
    EventBytes e; e.anim_type = static_cast<uint8_t>(AnimType::Shift);
    e.start = 0.0f; e.duration = 1.0f;
    e.dst = 0; e.src = 1; e.work = 2;
    e.params = shift_params(1, 1.0f, 1, 0, 0, 0, 0);
    append_event(b, e);
    CHECK(run(b) == DecodeError::InvalidField);
}

TEST_CASE("decode_program: shift event with mismatched src/work sizes rejected", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.buffer_count = 3; h.pixel_view_count = 3; h.layer_count = 1;
    append_prefix_and_header(b, h);
    append_buffer_sizes(b, { 4, 4, 8 });
    ViewBytes v0; v0.buffer_idx = 0; v0.size = 4;
    v0.storage_identity = true;
    v0.has_physical = true; v0.physical_identity = true;
    append_pixel_view(b, v0);
    ViewBytes v1; v1.buffer_idx = 1; v1.size = 4;
    v1.storage_identity = true;
    v1.has_physical = false;
    append_pixel_view(b, v1);
    ViewBytes v2; v2.buffer_idx = 2; v2.size = 8;
    v2.storage_identity = true;
    v2.has_physical = false;
    append_pixel_view(b, v2);
    put_u16(b, 1);
    EventBytes e; e.anim_type = static_cast<uint8_t>(AnimType::Shift);
    e.start = 0.0f; e.duration = 1.0f;
    e.dst = 0; e.src = 1; e.work = 2;
    e.params = shift_params(1, 1.0f, 1, 0, 0, 0, 0);
    append_event(b, e);
    CHECK(run(b) == DecodeError::InvalidField);
}

TEST_CASE("decode_program: paint constant mode with count 0 rejected", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.buffer_count = 1; h.pixel_view_count = 1; h.layer_count = 1;
    append_prefix_and_header(b, h);
    append_buffer_sizes(b, { 4 });
    ViewBytes v; v.buffer_idx = 0; v.size = 4;
    v.storage_identity = true;
    v.has_physical = true; v.physical_identity = true;
    append_pixel_view(b, v);
    put_u16(b, 1);
    EventBytes e; e.anim_type = static_cast<uint8_t>(AnimType::Paint);
    e.start = 0.0f; e.duration = 1.0f; e.dst = 0;
    e.params = paint_constant_params({});   // mode 1, count 0
    append_event(b, e);
    CHECK(run(b) == DecodeError::InvalidField);
}

TEST_CASE("decode_program: animation factory rejection propagates", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.buffer_count = 1; h.pixel_view_count = 1; h.layer_count = 1;
    append_prefix_and_header(b, h);
    append_buffer_sizes(b, { 4 });
    ViewBytes v; v.buffer_idx = 0; v.size = 4;
    v.storage_identity = true;
    v.has_physical = true; v.physical_identity = true;
    append_pixel_view(b, v);
    put_u16(b, 1);
    EventBytes e; e.anim_type = static_cast<uint8_t>(AnimType::Wave);
    e.start = 0.0f; e.duration = 1.0f; e.dst = 0;
    e.params = wave_params(99, 0, 0, 0, 0, 1, 1.0f, 0, 0);   // channel > 2
    append_event(b, e);
    CHECK(run(b) == DecodeError::InvalidField);
}

// ---------------------------------------------------------------------------
// Layers
// ---------------------------------------------------------------------------

TEST_CASE("decode_program: events out of order on a layer rejected", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h; h.buffer_count = 1; h.pixel_view_count = 1; h.layer_count = 1;
    append_prefix_and_header(b, h);
    append_buffer_sizes(b, { 4 });
    ViewBytes v; v.buffer_idx = 0; v.size = 4;
    v.storage_identity = true;
    v.has_physical = true; v.physical_identity = true;
    append_pixel_view(b, v);
    put_u16(b, 2);
    EventBytes e; e.anim_type = static_cast<uint8_t>(AnimType::Wave);
    e.dst = 0;
    e.params = wave_params(2, 0, 0, 0, 0, 1, 1.0f, 0, 0);
    e.start = 0.5f; e.duration = 0.4f;
    append_event(b, e);
    e.start = 0.4f; e.duration = 0.4f;   // overlaps prior event
    append_event(b, e);
    CHECK(run(b) == DecodeError::InvalidField);
}

// ---------------------------------------------------------------------------
// Trailing bytes
// ---------------------------------------------------------------------------

TEST_CASE("decode_program: trailing bytes rejected", "[decoder]") {
    auto bytes = build_minimal_valid_blob();
    bytes.push_back(0xAA);
    CHECK(run(bytes) == DecodeError::TrailingBytes);
}

// ---------------------------------------------------------------------------
// Successful round-trip
// ---------------------------------------------------------------------------

TEST_CASE("decode_program: minimal valid blob decodes", "[decoder]") {
    auto bytes = build_minimal_valid_blob();
    DecodeError err = DecodeError::OutOfMemory;
    Program* p = decode_program(bytes.data(), bytes.size(), 4, &err);
    REQUIRE(p != nullptr);
    REQUIRE(err == DecodeError::Ok);
    CHECK(p->duration == 1.0f);
    CHECK(p->target_fps == 50);
    CHECK(p->requires_sync == false);
    CHECK(p->layer_count == 1);
    CHECK(p->layers[0].count() == 1);
    CHECK(p->pixel_buffer_pool.buffer_count() == 1);
    CHECK(p->pixel_views.count() == 1);
    free_program(p);
}

TEST_CASE("decode_program: requires_sync flag carried into Program", "[decoder]") {
    auto bytes = build_minimal_valid_blob();
    bytes[5] = 0x01;   // flags: requires_sync = 1
    DecodeError err = DecodeError::Ok;
    Program* p = decode_program(bytes.data(), bytes.size(), 4, &err);
    REQUIRE(p != nullptr);
    CHECK(p->requires_sync == true);
    free_program(p);
}

TEST_CASE("decode_program: full program with copy ops decodes", "[decoder]") {
    std::vector<uint8_t> b;
    HeaderBytes h;
    h.buffer_count = 2;
    h.pixel_view_count = 2;
    h.copy_op_count = 1;
    h.layer_count = 1;
    h.duration = 1.0f;
    append_prefix_and_header(b, h);
    append_buffer_sizes(b, { 4, 4 });

    // View 0: storage on buf 0, has physical.
    ViewBytes v0;
    v0.buffer_idx = 0; v0.size = 4;
    v0.storage_identity = true;
    v0.has_physical = true; v0.physical_identity = true;
    append_pixel_view(b, v0);
    // View 1: storage on buf 1, no physical (used as src/dst of copy op).
    ViewBytes v1;
    v1.buffer_idx = 1; v1.size = 4;
    v1.storage_identity = true;
    v1.has_physical = false; v1.physical_identity = false;
    append_pixel_view(b, v1);

    append_copy_op(b, { 0.5f, 1, 1 });   // self-copy is OK as long as sizes match

    put_u16(b, 1);   // events on layer 0
    EventBytes e;
    e.anim_type = static_cast<uint8_t>(AnimType::Spark);
    e.start = 0.0f; e.duration = 1.0f; e.dst = 0;
    e.params = spark_params(60, 1, 1, 0.5f);
    append_event(b, e);

    DecodeError err = DecodeError::Ok;
    Program* p = decode_program(b.data(), b.size(), 4, &err);
    REQUIRE(p != nullptr);
    REQUIRE(err == DecodeError::Ok);
    CHECK(p->copy_ops.count() == 1);
    CHECK(p->layers[0].count() == 1);
    free_program(p);
}
