#include <catch2/catch_test_macros.hpp>
#include <catch2/catch_approx.hpp>
#include "../src/anim_wave.h"
#include "../src/anim_spark.h"
#include "../src/anim_shift.h"
#include "../src/anim_paint.h"

#include <cmath>
#include <cstring>

static const uint8_t LEN = 4;

// ---------------------------------------------------------------------------
// AnimWave
// ---------------------------------------------------------------------------

TEST_CASE("AnimWave: t=0 phase0=0 gives midpoint value", "[anim][wave]") {
    WaveParams p = {};
    p.channel = 2;  // V
    p.h = 0; p.s = 0; p.v = 0;
    p.min_val = 0.0f; p.max_val = 1.0f;
    p.period = 1.0f; p.phase0 = 0.0f; p.pixel_step = 0.0f;

    AnimWave wave(p);
    hsva_t buf[LEN];
    wave.render(buf, LEN, 0.0f);

    // sin(0)*0.5+0.5 = 0.5 -> val = 0 + 1*0.5 = 0.5
    for (uint8_t i = 0; i < LEN; i++) {
        CHECK(Catch::Approx(buf[i].v).epsilon(1e-4) == 0.5f);
        CHECK(Catch::Approx(buf[i].a).epsilon(1e-4) == 1.0f);
    }
}

TEST_CASE("AnimWave: channel=0 modulates H", "[anim][wave]") {
    WaveParams p = {};
    p.channel = 0;  // H
    p.h = 999.0f; p.s = 0.5f; p.v = 0.8f;
    p.min_val = 0.0f; p.max_val = 360.0f;
    p.period = 1.0f; p.phase0 = 0.0f; p.pixel_step = 0.0f;

    AnimWave wave(p);
    hsva_t buf[LEN];
    wave.render(buf, LEN, 0.0f);

    // H = 0 + 360*(sin(0)*0.5+0.5) = 180
    CHECK(Catch::Approx(buf[0].h).epsilon(1e-4) == 180.0f);
    // S and V stay fixed
    CHECK(Catch::Approx(buf[0].s).epsilon(1e-4) == 0.5f);
    CHECK(Catch::Approx(buf[0].v).epsilon(1e-4) == 0.8f);
}

TEST_CASE("AnimWave: channel=1 modulates S", "[anim][wave]") {
    WaveParams p = {};
    p.channel = 1;  // S
    p.h = 120.0f; p.s = 999.0f; p.v = 0.8f;
    p.min_val = 0.0f; p.max_val = 1.0f;
    p.period = 1.0f; p.phase0 = 0.0f; p.pixel_step = 0.0f;

    AnimWave wave(p);
    hsva_t buf[LEN];
    wave.render(buf, LEN, 0.0f);

    CHECK(Catch::Approx(buf[0].h).epsilon(1e-4) == 120.0f);
    CHECK(Catch::Approx(buf[0].s).epsilon(1e-4) == 0.5f);
    CHECK(Catch::Approx(buf[0].v).epsilon(1e-4) == 0.8f);
}

TEST_CASE("AnimWave: all pixels get alpha=1.0", "[anim][wave]") {
    WaveParams p = {};
    p.channel = 2; p.h = 0; p.s = 0; p.v = 0;
    p.min_val = 0.0f; p.max_val = 1.0f;
    p.period = 1.0f; p.phase0 = 0.0f; p.pixel_step = 1.0f;

    AnimWave wave(p);
    hsva_t buf[LEN];
    wave.render(buf, LEN, 0.5f);

    for (uint8_t i = 0; i < LEN; i++) {
        CHECK(Catch::Approx(buf[i].a).epsilon(1e-4) == 1.0f);
    }
}

// ---------------------------------------------------------------------------
// AnimSpark
// ---------------------------------------------------------------------------

TEST_CASE("AnimSpark: t=0 alpha=1.0", "[anim][spark]") {
    SparkParams p = {};
    p.color_h = 120.0f; p.color_s = 1.0f; p.color_v = 1.0f;
    p.fade = 1.0f;

    AnimSpark spark(p);
    hsva_t buf[LEN];
    spark.render(buf, LEN, 0.0f);

    for (uint8_t i = 0; i < LEN; i++) {
        CHECK(Catch::Approx(buf[i].a).epsilon(1e-4) == 1.0f);
        CHECK(Catch::Approx(buf[i].h).epsilon(1e-4) == 120.0f);
        CHECK(Catch::Approx(buf[i].s).epsilon(1e-4) == 1.0f);
        CHECK(Catch::Approx(buf[i].v).epsilon(1e-4) == 1.0f);
    }
}

TEST_CASE("AnimSpark: t=fade alpha=0.0", "[anim][spark]") {
    SparkParams p = {};
    p.color_h = 0; p.color_s = 1; p.color_v = 1;
    p.fade = 1.0f;

    AnimSpark spark(p);
    hsva_t buf[LEN];
    spark.render(buf, LEN, 1.0f);

    for (uint8_t i = 0; i < LEN; i++) {
        CHECK(Catch::Approx(buf[i].a).epsilon(1e-4) == 0.0f);
    }
}

TEST_CASE("AnimSpark: t=fade/2 quadratic ease-out", "[anim][spark]") {
    SparkParams p = {};
    p.color_h = 0; p.color_s = 1; p.color_v = 1;
    p.fade = 1.0f;

    AnimSpark spark(p);
    hsva_t buf[LEN];
    spark.render(buf, LEN, 0.5f);

    // alpha = (1 - 0.5)^2 = 0.25
    for (uint8_t i = 0; i < LEN; i++) {
        CHECK(Catch::Approx(buf[i].a).epsilon(1e-4) == 0.25f);
    }
}

TEST_CASE("AnimSpark: uniform fill, all pixels same", "[anim][spark]") {
    SparkParams p = {};
    p.color_h = 60.0f; p.color_s = 0.8f; p.color_v = 0.6f;
    p.fade = 2.0f;

    AnimSpark spark(p);
    hsva_t buf[8];
    spark.render(buf, 8, 0.5f);

    for (uint8_t i = 1; i < 8; i++) {
        CHECK(buf[i].h == buf[0].h);
        CHECK(buf[i].s == buf[0].s);
        CHECK(buf[i].v == buf[0].v);
        CHECK(buf[i].a == buf[0].a);
    }
}

// ---------------------------------------------------------------------------
// AnimShift
// ---------------------------------------------------------------------------

TEST_CASE("AnimShift: t=0 output equals work buffer", "[anim][shift]") {
    hsva_t work[LEN] = {
        {10, 0.1f, 0.1f, 1.0f}, {20, 0.2f, 0.2f, 1.0f},
        {30, 0.3f, 0.3f, 1.0f}, {40, 0.4f, 0.4f, 1.0f}
    };
    ShiftParams p = {};
    p.direction = 1; p.velocity = 1.0f; p.circular = 0;
    p.fill_h = 0; p.fill_s = 0; p.fill_v = 0; p.fill_a = 0;

    AnimShift shift(p, work, LEN);
    hsva_t buf[LEN];
    shift.render(buf, LEN, 0.0f);

    for (uint8_t i = 0; i < LEN; i++) {
        CHECK(buf[i].h == work[i].h);
        CHECK(buf[i].s == work[i].s);
        CHECK(buf[i].v == work[i].v);
        CHECK(buf[i].a == work[i].a);
    }
}

TEST_CASE("AnimShift: right shift by 1 pixel", "[anim][shift]") {
    hsva_t work[LEN] = {
        {10, 0.1f, 0.1f, 1.0f}, {20, 0.2f, 0.2f, 1.0f},
        {30, 0.3f, 0.3f, 1.0f}, {40, 0.4f, 0.4f, 1.0f}
    };
    ShiftParams p = {};
    p.direction = 1; p.velocity = 1.0f; p.circular = 0;
    p.fill_h = 0; p.fill_s = 0; p.fill_v = 0; p.fill_a = 0;

    AnimShift shift(p, work, LEN);
    hsva_t buf[LEN];
    shift.render(buf, LEN, 1.0f);

    // pixel 0: src = 0-1 = -1 -> fill
    CHECK(buf[0].a == 0.0f);
    // pixel 1: src = 1-1 = 0 -> work[0]
    CHECK(buf[1].h == 10.0f);
    // pixel 2: src = 2-1 = 1 -> work[1]
    CHECK(buf[2].h == 20.0f);
    // pixel 3: src = 3-1 = 2 -> work[2]
    CHECK(buf[3].h == 30.0f);
}

TEST_CASE("AnimShift: left shift by 1 pixel", "[anim][shift]") {
    hsva_t work[LEN] = {
        {10, 0.1f, 0.1f, 1.0f}, {20, 0.2f, 0.2f, 1.0f},
        {30, 0.3f, 0.3f, 1.0f}, {40, 0.4f, 0.4f, 1.0f}
    };
    ShiftParams p = {};
    p.direction = 0; p.velocity = 1.0f; p.circular = 0;
    p.fill_h = 0; p.fill_s = 0; p.fill_v = 0; p.fill_a = 0;

    AnimShift shift(p, work, LEN);
    hsva_t buf[LEN];
    shift.render(buf, LEN, 1.0f);

    // offset = -1. src_f = i - (-1) = i+1
    // pixel 0: src = 1 -> work[1]
    CHECK(buf[0].h == 20.0f);
    // pixel 1: src = 2 -> work[2]
    CHECK(buf[1].h == 30.0f);
    // pixel 2: src = 3 -> work[3]
    CHECK(buf[2].h == 40.0f);
    // pixel 3: src = 4 -> out of bounds -> fill
    CHECK(buf[3].a == 0.0f);
}

TEST_CASE("AnimShift: circular wraps around", "[anim][shift]") {
    hsva_t work[LEN] = {
        {10, 0.1f, 0.1f, 1.0f}, {20, 0.2f, 0.2f, 1.0f},
        {30, 0.3f, 0.3f, 1.0f}, {40, 0.4f, 0.4f, 1.0f}
    };
    ShiftParams p = {};
    p.direction = 1; p.velocity = 1.0f; p.circular = 1;
    p.fill_h = 0; p.fill_s = 0; p.fill_v = 0; p.fill_a = 0;

    AnimShift shift(p, work, LEN);
    hsva_t buf[LEN];
    shift.render(buf, LEN, 1.0f);

    // pixel 0: src = -1 -> wrap -> work[3]
    CHECK(buf[0].h == 40.0f);
    // pixel 1: src = 0 -> work[0]
    CHECK(buf[1].h == 10.0f);
    // pixel 2: src = 1 -> work[1]
    CHECK(buf[2].h == 20.0f);
    // pixel 3: src = 2 -> work[2]
    CHECK(buf[3].h == 30.0f);
}

TEST_CASE("AnimShift: fill color for out-of-bounds pixels", "[anim][shift]") {
    hsva_t work[LEN] = {
        {10, 0.1f, 0.1f, 1.0f}, {20, 0.2f, 0.2f, 1.0f},
        {30, 0.3f, 0.3f, 1.0f}, {40, 0.4f, 0.4f, 1.0f}
    };
    ShiftParams p = {};
    p.direction = 1; p.velocity = 2.0f; p.circular = 0;
    p.fill_h = 99.0f; p.fill_s = 0.99f; p.fill_v = 0.99f; p.fill_a = 1.0f;

    AnimShift shift(p, work, LEN);
    hsva_t buf[LEN];
    shift.render(buf, LEN, 1.0f);

    // offset = 2. pixel 0: src = -2, pixel 1: src = -1 -> both fill
    CHECK(buf[0].h == 99.0f);
    CHECK(buf[0].a == 1.0f);
    CHECK(buf[1].h == 99.0f);
    CHECK(buf[1].a == 1.0f);
    // pixel 2: src = 0 -> work[0]
    CHECK(buf[2].h == 10.0f);
}

// ---------------------------------------------------------------------------
// AnimPaint
// ---------------------------------------------------------------------------

TEST_CASE("AnimPaint: solid mode fills all pixels", "[anim][paint]") {
    PaintParams p = {};
    p.mode = 0;
    p.color_h = 180.0f; p.color_s = 0.5f; p.color_v = 0.8f; p.color_a = 1.0f;

    AnimPaint paint(p);
    hsva_t buf[LEN];
    paint.render(buf, LEN, 0.0f);

    for (uint8_t i = 0; i < LEN; i++) {
        CHECK(buf[i].h == 180.0f);
        CHECK(buf[i].s == 0.5f);
        CHECK(buf[i].v == 0.8f);
        CHECK(buf[i].a == 1.0f);
    }
}

TEST_CASE("AnimPaint: per-pixel mode copies source", "[anim][paint]") {
    hsva_t pixels[LEN] = {
        {10, 0.1f, 0.1f, 1.0f}, {20, 0.2f, 0.2f, 0.5f},
        {30, 0.3f, 0.3f, 0.3f}, {40, 0.4f, 0.4f, 0.0f}
    };
    PaintParams p = {};
    p.mode = 1;
    p.pixel_count = LEN;
    p.pixels = pixels;

    AnimPaint paint(p);
    hsva_t buf[LEN];
    paint.render(buf, LEN, 0.0f);

    for (uint8_t i = 0; i < LEN; i++) {
        CHECK(buf[i].h == pixels[i].h);
        CHECK(buf[i].s == pixels[i].s);
        CHECK(buf[i].v == pixels[i].v);
        CHECK(buf[i].a == pixels[i].a);
    }
}

TEST_CASE("AnimPaint: per-pixel with fewer pixels than buffer", "[anim][paint]") {
    hsva_t pixels[2] = {
        {10, 0.1f, 0.1f, 1.0f}, {20, 0.2f, 0.2f, 0.5f}
    };
    PaintParams p = {};
    p.mode = 1;
    p.pixel_count = 2;
    p.pixels = pixels;

    AnimPaint paint(p);
    hsva_t buf[LEN];
    memset(buf, 0, sizeof(buf));
    paint.render(buf, LEN, 0.0f);

    // First 2 pixels copied
    CHECK(buf[0].h == 10.0f);
    CHECK(buf[1].h == 20.0f);
    // Remaining untouched
    CHECK(buf[2].h == 0.0f);
    CHECK(buf[3].h == 0.0f);
}
