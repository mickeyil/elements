#include <catch2/catch_test_macros.hpp>
#include <catch2/catch_approx.hpp>

#include <cmath>
#include <cstdint>
#include <cstring>
#include <vector>

#include "../src/animations/paint.h"
#include "../src/animations/shift.h"
#include "../src/animations/spark.h"
#include "../src/animations/wave.h"
#include "../src/animation.h"
#include "../src/blob_reader.h"
#include "../src/colors.h"
#include "../src/pixel_view.h"

using Catch::Approx;

namespace {

// Bind a PixelView with identity storage and no physical mapping. Animations
// only touch dst by logical index, so this is the minimal setup.
void init_view(PixelView& v, hsva_t* buf, uint16_t size) {
    REQUIRE(v.initialize(buf, size));
}

void append_f32(std::vector<uint8_t>& out, float f) {
    uint8_t bytes[4];
    std::memcpy(bytes, &f, 4);
    out.insert(out.end(), bytes, bytes + 4);
}

std::vector<uint8_t> pack_wave(uint8_t channel, float h, float s, float v,
                                float min_val, float max_val,
                                float period, float phase0, float pixel_step) {
    std::vector<uint8_t> out;
    out.push_back(channel);
    append_f32(out, h);
    append_f32(out, s);
    append_f32(out, v);
    append_f32(out, min_val);
    append_f32(out, max_val);
    append_f32(out, period);
    append_f32(out, phase0);
    append_f32(out, pixel_step);
    return out;
}

std::vector<uint8_t> pack_spark(float h, float s, float v, float fade) {
    std::vector<uint8_t> out;
    append_f32(out, h);
    append_f32(out, s);
    append_f32(out, v);
    append_f32(out, fade);
    return out;
}

std::vector<uint8_t> pack_paint_solid(float h, float s, float v, float a) {
    std::vector<uint8_t> out;
    out.push_back(0);  // mode = solid
    append_f32(out, h);
    append_f32(out, s);
    append_f32(out, v);
    append_f32(out, a);
    return out;
}

std::vector<uint8_t> pack_paint_per_pixel(const std::vector<hsva_t>& pixels) {
    std::vector<uint8_t> out;
    out.push_back(1);  // mode = per_pixel
    // count is u16 little-endian
    out.push_back(static_cast<uint8_t>(pixels.size() & 0xFF));
    out.push_back(static_cast<uint8_t>((pixels.size() >> 8) & 0xFF));
    for (const auto& p : pixels) {
        append_f32(out, p.h);
        append_f32(out, p.s);
        append_f32(out, p.v);
        append_f32(out, p.a);
    }
    return out;
}

std::vector<uint8_t> pack_shift(uint8_t direction, float velocity,
                                 uint8_t circular,
                                 float fill_h, float fill_s, float fill_v,
                                 float fill_a) {
    std::vector<uint8_t> out;
    out.push_back(direction);
    append_f32(out, velocity);
    out.push_back(circular);
    append_f32(out, fill_h);
    append_f32(out, fill_s);
    append_f32(out, fill_v);
    append_f32(out, fill_a);
    return out;
}

}  // namespace

// ===========================================================================
// Wave
// ===========================================================================

TEST_CASE("Wave: t=0 phase0=0 channel=V gives midpoint", "[anim][wave]") {
    WaveParams p = {};
    p.channel = 2;  // V
    p.h = 0; p.s = 0; p.v = 0;
    p.min_val = 0.0f; p.max_val = 1.0f;
    p.period = 1.0f; p.phase0 = 0.0f; p.pixel_step = 0.0f;

    Wave wave(p);
    hsva_t buf[4];
    PixelView dst; init_view(dst, buf, 4);
    wave.render(dst, 0.0f);

    // sin(0)*0.5+0.5 = 0.5 -> v = 0.5
    for (uint16_t i = 0; i < 4; i++) {
        CHECK(buf[i].v == Approx(0.5f).epsilon(1e-4));
        CHECK(buf[i].a == Approx(1.0f).epsilon(1e-4));
    }
}

TEST_CASE("Wave: channel selector picks the modulated component", "[anim][wave]") {
    WaveParams p = {};
    p.h = 200.0f; p.s = 0.5f; p.v = 0.5f;
    p.min_val = 0.0f; p.max_val = 1.0f;
    p.period = 4.0f; p.phase0 = 0.0f; p.pixel_step = 0.0f;

    SECTION("channel=H") {
        p.channel = 0; p.min_val = 0.0f; p.max_val = 360.0f;
        Wave wave(p);
        hsva_t buf[1]; PixelView dst; init_view(dst, buf, 1);
        wave.render(dst, 1.0f);  // sin(pi/2)=1 -> val=360
        CHECK(buf[0].h == Approx(360.0f).epsilon(1e-4));
        CHECK(buf[0].s == Approx(0.5f).epsilon(1e-4));
        CHECK(buf[0].v == Approx(0.5f).epsilon(1e-4));
    }
    SECTION("channel=S") {
        p.channel = 1; p.min_val = 0.0f; p.max_val = 1.0f;
        Wave wave(p);
        hsva_t buf[1]; PixelView dst; init_view(dst, buf, 1);
        wave.render(dst, 1.0f);  // sin(pi/2)=1 -> val=1
        CHECK(buf[0].h == Approx(200.0f).epsilon(1e-4));
        CHECK(buf[0].s == Approx(1.0f).epsilon(1e-4));
        CHECK(buf[0].v == Approx(0.5f).epsilon(1e-4));
    }
    SECTION("channel=V") {
        p.channel = 2; p.min_val = 0.0f; p.max_val = 1.0f;
        Wave wave(p);
        hsva_t buf[1]; PixelView dst; init_view(dst, buf, 1);
        wave.render(dst, 1.0f);
        CHECK(buf[0].h == Approx(200.0f).epsilon(1e-4));
        CHECK(buf[0].s == Approx(0.5f).epsilon(1e-4));
        CHECK(buf[0].v == Approx(1.0f).epsilon(1e-4));
    }
}

TEST_CASE("Wave: pixel_step shifts phase across pixels", "[anim][wave]") {
    WaveParams p = {};
    p.channel = 2;
    p.h = 0; p.s = 0; p.v = 0;
    p.min_val = 0.0f; p.max_val = 1.0f;
    p.period = 4.0f; p.phase0 = 0.0f;
    p.pixel_step = static_cast<float>(M_PI) / 2.0f;  // 90deg per pixel

    Wave wave(p);
    hsva_t buf[4]; PixelView dst; init_view(dst, buf, 4);
    wave.render(dst, 0.0f);  // base_phase = 0

    // Pixel i has phase i*pi/2: sin gives 0, 1, 0, -1; rescaled to [0,1] gives 0.5, 1, 0.5, 0
    CHECK(buf[0].v == Approx(0.5f).epsilon(1e-4));
    CHECK(buf[1].v == Approx(1.0f).epsilon(1e-4));
    CHECK(buf[2].v == Approx(0.5f).epsilon(1e-4));
    CHECK(buf[3].v == Approx(0.0f).margin(1e-4));
}

TEST_CASE("Wave: from_blob round-trip produces equivalent render", "[anim][wave][from_blob]") {
    auto bytes = pack_wave(2, 0, 0, 0, 0.0f, 1.0f, 1.0f, 0.0f, 0.0f);
    DecodeError err = DecodeError::OutOfMemory;
    Animation* anim = Wave::from_blob(bytes.data(), bytes.size(), &err);
    REQUIRE(anim != nullptr);
    REQUIRE(err == DecodeError::Ok);

    hsva_t buf[2]; PixelView dst; init_view(dst, buf, 2);
    anim->render(dst, 0.0f);
    CHECK(buf[0].v == Approx(0.5f).epsilon(1e-4));

    delete anim;
}

TEST_CASE("Wave: from_blob rejects channel > 2", "[anim][wave][from_blob]") {
    auto bytes = pack_wave(3, 0, 0, 0, 0, 1, 1, 0, 0);
    DecodeError err = DecodeError::Ok;
    CHECK(Wave::from_blob(bytes.data(), bytes.size(), &err) == nullptr);
    CHECK(err == DecodeError::InvalidField);
}

TEST_CASE("Wave: from_blob rejects period <= 0", "[anim][wave][from_blob]") {
    auto bytes = pack_wave(2, 0, 0, 0, 0, 1, 0.0f, 0, 0);
    DecodeError err = DecodeError::Ok;
    CHECK(Wave::from_blob(bytes.data(), bytes.size(), &err) == nullptr);
    CHECK(err == DecodeError::InvalidField);
}

TEST_CASE("Wave: from_blob rejects NaN", "[anim][wave][from_blob]") {
    auto bytes = pack_wave(2, std::nanf(""), 0, 0, 0, 1, 1, 0, 0);
    DecodeError err = DecodeError::Ok;
    CHECK(Wave::from_blob(bytes.data(), bytes.size(), &err) == nullptr);
    CHECK(err == DecodeError::InvalidField);
}

TEST_CASE("Wave: from_blob rejects truncated bytes", "[anim][wave][from_blob]") {
    auto bytes = pack_wave(2, 0, 0, 0, 0, 1, 1, 0, 0);
    bytes.pop_back();
    DecodeError err = DecodeError::Ok;
    CHECK(Wave::from_blob(bytes.data(), bytes.size(), &err) == nullptr);
    CHECK(err == DecodeError::InvalidField);
}

// ===========================================================================
// Spark
// ===========================================================================

TEST_CASE("Spark: t=0 gives full alpha", "[anim][spark]") {
    SparkParams p = {};
    p.color_h = 60.0f; p.color_s = 1.0f; p.color_v = 1.0f;
    p.fade = 1.0f;

    Spark spark(p);
    hsva_t buf[2]; PixelView dst; init_view(dst, buf, 2);
    spark.render(dst, 0.0f);

    for (uint16_t i = 0; i < 2; i++) {
        CHECK(buf[i].h == Approx(60.0f));
        CHECK(buf[i].s == Approx(1.0f));
        CHECK(buf[i].v == Approx(1.0f));
        CHECK(buf[i].a == Approx(1.0f));
    }
}

TEST_CASE("Spark: alpha follows quadratic ease-out", "[anim][spark]") {
    SparkParams p = {};
    p.fade = 1.0f;
    Spark spark(p);
    hsva_t buf[1]; PixelView dst; init_view(dst, buf, 1);

    spark.render(dst, 0.5f);  // (1 - 0.5)^2 = 0.25
    CHECK(buf[0].a == Approx(0.25f).epsilon(1e-4));

    spark.render(dst, 1.0f);  // alpha clamps to 0 at fade
    CHECK(buf[0].a == Approx(0.0f).margin(1e-4));

    spark.render(dst, 5.0f);  // past fade, still 0
    CHECK(buf[0].a == Approx(0.0f).margin(1e-4));
}

TEST_CASE("Spark: from_blob round-trip", "[anim][spark][from_blob]") {
    auto bytes = pack_spark(120.0f, 0.5f, 1.0f, 2.0f);
    DecodeError err = DecodeError::Ok;
    Animation* anim = Spark::from_blob(bytes.data(), bytes.size(), &err);
    REQUIRE(anim != nullptr);
    REQUIRE(err == DecodeError::Ok);

    hsva_t buf[1]; PixelView dst; init_view(dst, buf, 1);
    anim->render(dst, 0.0f);
    CHECK(buf[0].h == Approx(120.0f));
    CHECK(buf[0].a == Approx(1.0f));
    delete anim;
}

TEST_CASE("Spark: from_blob rejects fade <= 0", "[anim][spark][from_blob]") {
    auto bytes = pack_spark(0, 0, 0, 0.0f);
    DecodeError err = DecodeError::Ok;
    CHECK(Spark::from_blob(bytes.data(), bytes.size(), &err) == nullptr);
    CHECK(err == DecodeError::InvalidField);
}

TEST_CASE("Spark: from_blob rejects NaN", "[anim][spark][from_blob]") {
    auto bytes = pack_spark(0, 0, 0, std::nanf(""));
    DecodeError err = DecodeError::Ok;
    CHECK(Spark::from_blob(bytes.data(), bytes.size(), &err) == nullptr);
    CHECK(err == DecodeError::InvalidField);
}

// ===========================================================================
// Paint
// ===========================================================================

TEST_CASE("Paint: solid mode fills every pixel", "[anim][paint]") {
    Paint paint(60.0f, 0.5f, 1.0f, 0.75f);
    hsva_t buf[3]; PixelView dst; init_view(dst, buf, 3);
    paint.render(dst, 0.0f);
    for (uint16_t i = 0; i < 3; i++) {
        CHECK(buf[i].h == Approx(60.0f));
        CHECK(buf[i].s == Approx(0.5f));
        CHECK(buf[i].v == Approx(1.0f));
        CHECK(buf[i].a == Approx(0.75f));
    }
}

TEST_CASE("Paint: constant mode replays the array into dst", "[anim][paint]") {
    // Contract: count == dst.size() (compiler enforces, decoder asserts).
    hsva_t* constant = new hsva_t[3];
    constant[0] = hsva_t(0,   1, 1, 1);
    constant[1] = hsva_t(120, 1, 1, 1);
    constant[2] = hsva_t(240, 1, 1, 1);

    Paint paint(constant, 3);
    hsva_t buf[3]; PixelView dst; init_view(dst, buf, 3);
    paint.render(dst, 0.0f);

    CHECK(buf[0].h == Approx(0.0f));
    CHECK(buf[1].h == Approx(120.0f));
    CHECK(buf[2].h == Approx(240.0f));
}

TEST_CASE("Paint: constant_array_size reports count in constant mode, 0 in solid",
          "[anim][paint]") {
    Paint solid(60.0f, 1.0f, 1.0f, 1.0f);
    CHECK(solid.constant_array_size() == 0);

    hsva_t* constant = new hsva_t[2];
    constant[0] = hsva_t(0, 1, 1, 1);
    constant[1] = hsva_t(120, 1, 1, 1);
    Paint constant_paint(constant, 2);
    CHECK(constant_paint.constant_array_size() == 2);
}

TEST_CASE("Paint: from_blob solid round-trip", "[anim][paint][from_blob]") {
    auto bytes = pack_paint_solid(60.0f, 0.25f, 0.5f, 0.75f);
    DecodeError err = DecodeError::Ok;
    Animation* anim = Paint::from_blob(bytes.data(), bytes.size(), &err);
    REQUIRE(anim != nullptr);
    REQUIRE(err == DecodeError::Ok);

    hsva_t buf[1]; PixelView dst; init_view(dst, buf, 1);
    anim->render(dst, 0.0f);
    CHECK(buf[0].h == Approx(60.0f));
    CHECK(buf[0].s == Approx(0.25f));
    CHECK(buf[0].v == Approx(0.5f));
    CHECK(buf[0].a == Approx(0.75f));
    delete anim;
}

TEST_CASE("Paint: from_blob constant round-trip", "[anim][paint][from_blob]") {
    std::vector<hsva_t> constant = {
        hsva_t(0,   1, 1, 1),
        hsva_t(120, 1, 1, 1),
    };
    auto bytes = pack_paint_per_pixel(constant);
    DecodeError err = DecodeError::Ok;
    Animation* anim = Paint::from_blob(bytes.data(), bytes.size(), &err);
    REQUIRE(anim != nullptr);
    REQUIRE(err == DecodeError::Ok);

    hsva_t buf[2]; PixelView dst; init_view(dst, buf, 2);
    anim->render(dst, 0.0f);
    CHECK(buf[0].h == Approx(0.0f));
    CHECK(buf[1].h == Approx(120.0f));
    delete anim;
}

TEST_CASE("Paint: from_blob rejects unknown mode", "[anim][paint][from_blob]") {
    std::vector<uint8_t> bytes = { 2 };  // mode 2 is not defined
    DecodeError err = DecodeError::Ok;
    CHECK(Paint::from_blob(bytes.data(), bytes.size(), &err) == nullptr);
    CHECK(err == DecodeError::InvalidField);
}

TEST_CASE("Paint: from_blob rejects NaN", "[anim][paint][from_blob]") {
    auto bytes = pack_paint_solid(std::nanf(""), 0, 0, 1);
    DecodeError err = DecodeError::Ok;
    CHECK(Paint::from_blob(bytes.data(), bytes.size(), &err) == nullptr);
    CHECK(err == DecodeError::InvalidField);
}

TEST_CASE("Paint: from_blob rejects constant mode with count 0", "[anim][paint][from_blob]") {
    // Mode 1 (constant) with count 0 would produce a Paint that holds nullptr
    // and dereferences it on render. The factory must reject upfront.
    std::vector<uint8_t> bytes = { 1, 0, 0 };  // mode 1, u16 count = 0
    DecodeError err = DecodeError::Ok;
    CHECK(Paint::from_blob(bytes.data(), bytes.size(), &err) == nullptr);
    CHECK(err == DecodeError::InvalidField);
}

TEST_CASE("Paint: from_blob rejects a count larger than the params payload",
          "[anim][paint][from_blob]") {
    // Mode 1 with a huge count but only one pixel of data must be rejected
    // before allocating count hsva entries.
    std::vector<uint8_t> bytes = { 1, 0xE8, 0x03 };  // mode 1, u16 count = 1000
    append_f32(bytes, 0); append_f32(bytes, 1); append_f32(bytes, 1); append_f32(bytes, 1);
    DecodeError err = DecodeError::Ok;
    CHECK(Paint::from_blob(bytes.data(), bytes.size(), &err) == nullptr);
    CHECK(err == DecodeError::InvalidField);
}

// ===========================================================================
// Shift
// ===========================================================================

TEST_CASE("Shift: initialize copies src into work", "[anim][shift]") {
    ShiftParams p = {};
    Shift shift(p);

    hsva_t src_buf[4] = {
        hsva_t(10, 1, 1, 1),
        hsva_t(20, 1, 1, 1),
        hsva_t(30, 1, 1, 1),
        hsva_t(40, 1, 1, 1),
    };
    hsva_t work_buf[4] = {};
    PixelView src; init_view(src, src_buf, 4);
    PixelView work; init_view(work, work_buf, 4);

    shift.initialize(&src, &work);

    for (uint16_t i = 0; i < 4; i++) {
        CHECK(work_buf[i].h == src_buf[i].h);
    }
}

TEST_CASE("Shift: render at t=0 produces work contents (no offset)",
          "[anim][shift]") {
    ShiftParams p = {};
    p.direction = 1;       // right (sign doesn't matter at t=0)
    p.velocity = 1.0f;
    p.circular = 1;
    Shift shift(p);

    hsva_t src_buf[3] = { hsva_t(10,1,1,1), hsva_t(20,1,1,1), hsva_t(30,1,1,1) };
    hsva_t work_buf[3] = {};
    PixelView src; init_view(src, src_buf, 3);
    PixelView work; init_view(work, work_buf, 3);
    shift.initialize(&src, &work);

    hsva_t dst_buf[3] = {};
    PixelView dst; init_view(dst, dst_buf, 3);
    shift.render(dst, 0.0f);

    for (uint16_t i = 0; i < 3; i++) {
        CHECK(dst_buf[i].h == src_buf[i].h);
    }
}

TEST_CASE("Shift: positive velocity, direction=right shifts right",
          "[anim][shift]") {
    ShiftParams p = {};
    p.direction = 1;       // right (offset positive)
    p.velocity = 1.0f;     // 1 pixel/sec
    p.circular = 1;        // wrap so we don't hit fill
    Shift shift(p);

    hsva_t src_buf[3] = { hsva_t(10,1,1,1), hsva_t(20,1,1,1), hsva_t(30,1,1,1) };
    hsva_t work_buf[3] = {};
    PixelView src; init_view(src, src_buf, 3);
    PixelView work; init_view(work, work_buf, 3);
    shift.initialize(&src, &work);

    hsva_t dst_buf[3] = {};
    PixelView dst; init_view(dst, dst_buf, 3);
    shift.render(dst, 1.0f);  // offset = +1 -> dst[i] = work[i-1]

    CHECK(dst_buf[0].h == work_buf[2].h);  // wrap: work[(0-1) mod 3] = work[2]
    CHECK(dst_buf[1].h == work_buf[0].h);
    CHECK(dst_buf[2].h == work_buf[1].h);
}

TEST_CASE("Shift: positive velocity, direction=left shifts left",
          "[anim][shift]") {
    ShiftParams p = {};
    p.direction = 0;       // left (offset negated to negative)
    p.velocity = 1.0f;
    p.circular = 1;
    Shift shift(p);

    hsva_t src_buf[3] = { hsva_t(10,1,1,1), hsva_t(20,1,1,1), hsva_t(30,1,1,1) };
    hsva_t work_buf[3] = {};
    PixelView src; init_view(src, src_buf, 3);
    PixelView work; init_view(work, work_buf, 3);
    shift.initialize(&src, &work);

    hsva_t dst_buf[3] = {};
    PixelView dst; init_view(dst, dst_buf, 3);
    shift.render(dst, 1.0f);  // offset = -1 -> dst[i] = work[i+1]

    CHECK(dst_buf[0].h == work_buf[1].h);
    CHECK(dst_buf[1].h == work_buf[2].h);
    CHECK(dst_buf[2].h == work_buf[0].h);
}

TEST_CASE("Shift: non-circular fills exposed pixels", "[anim][shift]") {
    ShiftParams p = {};
    p.direction = 1;
    p.velocity = 1.0f;
    p.circular = 0;
    p.fill_h = 99.0f; p.fill_s = 0.5f; p.fill_v = 0.5f; p.fill_a = 0.5f;
    Shift shift(p);

    hsva_t src_buf[3] = { hsva_t(10,1,1,1), hsva_t(20,1,1,1), hsva_t(30,1,1,1) };
    hsva_t work_buf[3] = {};
    PixelView src; init_view(src, src_buf, 3);
    PixelView work; init_view(work, work_buf, 3);
    shift.initialize(&src, &work);

    hsva_t dst_buf[3] = {};
    PixelView dst; init_view(dst, dst_buf, 3);
    shift.render(dst, 1.0f);  // offset = +1 -> dst[0] = work[-1] (out of range -> fill)

    CHECK(dst_buf[0].h == 99.0f);              // filled
    CHECK(dst_buf[1].h == work_buf[0].h);
    CHECK(dst_buf[2].h == work_buf[1].h);
}

TEST_CASE("Shift: snapshot survives later src mutation", "[anim][shift]") {
    ShiftParams p = {};
    p.circular = 1;
    Shift shift(p);

    hsva_t src_buf[2] = { hsva_t(11,1,1,1), hsva_t(22,1,1,1) };
    hsva_t work_buf[2] = {};
    PixelView src; init_view(src, src_buf, 2);
    PixelView work; init_view(work, work_buf, 2);
    shift.initialize(&src, &work);

    // Mutate src after initialize; work is an independent snapshot.
    src_buf[0] = hsva_t(99, 0, 0, 0);
    src_buf[1] = hsva_t(88, 0, 0, 0);

    hsva_t dst_buf[2] = {};
    PixelView dst; init_view(dst, dst_buf, 2);
    shift.render(dst, 0.0f);

    CHECK(dst_buf[0].h == 11.0f);
    CHECK(dst_buf[1].h == 22.0f);
}

TEST_CASE("Shift: from_blob round-trip", "[anim][shift][from_blob]") {
    auto bytes = pack_shift(1, 2.0f, 1, 0.0f, 0.0f, 0.0f, 0.0f);
    DecodeError err = DecodeError::Ok;
    Animation* anim = Shift::from_blob(bytes.data(), bytes.size(), &err);
    REQUIRE(anim != nullptr);
    REQUIRE(err == DecodeError::Ok);
    delete anim;
}

TEST_CASE("Shift: from_blob rejects direction > 1", "[anim][shift][from_blob]") {
    auto bytes = pack_shift(2, 1.0f, 1, 0, 0, 0, 0);
    DecodeError err = DecodeError::Ok;
    CHECK(Shift::from_blob(bytes.data(), bytes.size(), &err) == nullptr);
    CHECK(err == DecodeError::InvalidField);
}

TEST_CASE("Shift: from_blob rejects circular > 1", "[anim][shift][from_blob]") {
    auto bytes = pack_shift(1, 1.0f, 2, 0, 0, 0, 0);
    DecodeError err = DecodeError::Ok;
    CHECK(Shift::from_blob(bytes.data(), bytes.size(), &err) == nullptr);
    CHECK(err == DecodeError::InvalidField);
}

TEST_CASE("Shift: from_blob rejects NaN velocity", "[anim][shift][from_blob]") {
    auto bytes = pack_shift(1, std::nanf(""), 1, 0, 0, 0, 0);
    DecodeError err = DecodeError::Ok;
    CHECK(Shift::from_blob(bytes.data(), bytes.size(), &err) == nullptr);
    CHECK(err == DecodeError::InvalidField);
}
