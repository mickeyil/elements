#include <catch2/catch_test_macros.hpp>
#include <catch2/catch_approx.hpp>
#include <cmath>
#include <cstdlib>
#include <initializer_list>
#include "core/colors.h"

// ---------------------------------------------------------------------------
// hsv_to_rgb
// ---------------------------------------------------------------------------

TEST_CASE("hsv_to_rgb: pure red", "[colors]") {
    rgb_t c = hsv_to_rgb(0.0f, 1.0f, 1.0f);
    CHECK(c.r == 255);
    CHECK(c.g == 0);
    CHECK(c.b == 0);
}

TEST_CASE("hsv_to_rgb: pure green", "[colors]") {
    rgb_t c = hsv_to_rgb(120.0f, 1.0f, 1.0f);
    CHECK(c.r == 0);
    CHECK(c.g == 255);
    CHECK(c.b == 0);
}

TEST_CASE("hsv_to_rgb: pure blue", "[colors]") {
    rgb_t c = hsv_to_rgb(240.0f, 1.0f, 1.0f);
    CHECK(c.r == 0);
    CHECK(c.g == 0);
    CHECK(c.b == 255);
}

TEST_CASE("hsv_to_rgb: white", "[colors]") {
    rgb_t c = hsv_to_rgb(0.0f, 0.0f, 1.0f);
    CHECK(c.r == 255);
    CHECK(c.g == 255);
    CHECK(c.b == 255);
}

TEST_CASE("hsv_to_rgb: black", "[colors]") {
    rgb_t c = hsv_to_rgb(0.0f, 0.0f, 0.0f);
    CHECK(c.r == 0);
    CHECK(c.g == 0);
    CHECK(c.b == 0);
}

TEST_CASE("hsv_to_rgb: hue wraps at 360", "[colors]") {
    rgb_t a = hsv_to_rgb(0.0f, 1.0f, 1.0f);
    rgb_t b = hsv_to_rgb(360.0f, 1.0f, 1.0f);
    CHECK(a.r == b.r);
    CHECK(a.g == b.g);
    CHECK(a.b == b.b);
}

TEST_CASE("hsv_to_rgb: hue 720 wraps to 0", "[colors]") {
    rgb_t a = hsv_to_rgb(0.0f, 1.0f, 1.0f);
    rgb_t b = hsv_to_rgb(720.0f, 1.0f, 1.0f);
    CHECK(a.r == b.r);
    CHECK(a.g == b.g);
    CHECK(a.b == b.b);
}

TEST_CASE("hsv_to_rgb: negative hue wraps", "[colors]") {
    rgb_t a = hsv_to_rgb(300.0f, 1.0f, 1.0f);
    rgb_t b = hsv_to_rgb(-60.0f, 1.0f, 1.0f);
    CHECK(a.r == b.r);
    CHECK(a.g == b.g);
    CHECK(a.b == b.b);
}

TEST_CASE("hsv_to_rgb: hue just below zero is red, not magenta", "[colors]") {
    // -5.96e-7 is what a hue Wave spanning -10..10 produces at its
    // half-period endpoint. Adding 360 to it rounds to exactly 360.
    for (float h : {-5.96e-7f, -1e-6f, -1e-5f, -1.5e-5f}) {
        CAPTURE(h);
        rgb_t c = hsv_to_rgb(h, 1.0f, 1.0f);
        CHECK(c.r == 255);
        CHECK(c.g == 0);
        CHECK(c.b == 0);
    }
}

TEST_CASE("hsv_to_rgb: out-of-range s and v clamp instead of overflowing", "[colors]") {
    rgb_t bright = hsv_to_rgb(0.0f, 1.0f, 1.2f);
    CHECK(bright.r == 255);
    CHECK(bright.g == 0);
    CHECK(bright.b == 0);

    rgb_t oversat = hsv_to_rgb(0.0f, 2.0f, 1.0f);
    CHECK(oversat.r == 255);
    CHECK(oversat.g == 0);
    CHECK(oversat.b == 0);

    rgb_t negative = hsv_to_rgb(0.0f, -1.0f, -0.5f);
    CHECK(negative.r == 0);
    CHECK(negative.g == 0);
    CHECK(negative.b == 0);
}

TEST_CASE("hsv_to_rgb: NaN channels count as zero", "[colors]") {
    rgb_t nan_v = hsv_to_rgb(0.0f, 1.0f, NAN);
    CHECK(nan_v.r == 0);
    CHECK(nan_v.g == 0);
    CHECK(nan_v.b == 0);

    rgb_t nan_h = hsv_to_rgb(NAN, 1.0f, 1.0f);
    CHECK(nan_h.r == 255);
    CHECK(nan_h.g == 0);
    CHECK(nan_h.b == 0);
}

TEST_CASE("hsv_to_rgb: grayscale at various V", "[colors]") {
    for (float v : {0.0f, 0.25f, 0.5f, 0.75f, 1.0f}) {
        rgb_t c = hsv_to_rgb(0.0f, 0.0f, v);
        uint8_t expected = (uint8_t)(v * 255.0f + 0.5f);
        CHECK(c.r == expected);
        CHECK(c.g == expected);
        CHECK(c.b == expected);
    }
}

// ---------------------------------------------------------------------------
// rgb_alpha_blend
// ---------------------------------------------------------------------------

TEST_CASE("rgb_alpha_blend: t=0 returns a", "[colors][alpha_blend]") {
    rgb_t a(100, 150, 200);
    rgb_t b(200, 50, 10);
    rgb_t c = rgb_alpha_blend(a, b, 0.0f);
    CHECK(c.r == a.r);
    CHECK(c.g == a.g);
    CHECK(c.b == a.b);
}

TEST_CASE("rgb_alpha_blend: t=1 returns b", "[colors][alpha_blend]") {
    rgb_t a(100, 150, 200);
    rgb_t b(200, 50, 10);
    rgb_t c = rgb_alpha_blend(a, b, 1.0f);
    CHECK(c.r == b.r);
    CHECK(c.g == b.g);
    CHECK(c.b == b.b);
}

TEST_CASE("rgb_alpha_blend: t=0.5 midpoint", "[colors][alpha_blend]") {
    rgb_t a(0, 0, 0);
    rgb_t b(200, 100, 50);
    rgb_t c = rgb_alpha_blend(a, b, 0.5f);
    CHECK(c.r == 100);
    CHECK(c.g == 50);
    CHECK(c.b == 25);
}

TEST_CASE("rgb_alpha_blend: t<0 clamps to a", "[colors][alpha_blend]") {
    rgb_t a(100, 150, 200);
    rgb_t b(200, 50, 10);
    rgb_t c = rgb_alpha_blend(a, b, -1.0f);
    CHECK(c.r == a.r);
    CHECK(c.g == a.g);
    CHECK(c.b == a.b);
}

TEST_CASE("rgb_alpha_blend: t>1 clamps to b", "[colors][alpha_blend]") {
    rgb_t a(100, 150, 200);
    rgb_t b(200, 50, 10);
    rgb_t c = rgb_alpha_blend(a, b, 2.0f);
    CHECK(c.r == b.r);
    CHECK(c.g == b.g);
    CHECK(c.b == b.b);
}

// ---------------------------------------------------------------------------
// rgb_to_hsv
// ---------------------------------------------------------------------------

TEST_CASE("rgb_to_hsv: primaries", "[colors][rgb_to_hsv]") {
    hsva_t red = rgb_to_hsv(rgb_t(255, 0, 0));
    CHECK(red.h == Catch::Approx(0.0f).margin(1e-4));
    CHECK(red.s == Catch::Approx(1.0f));
    CHECK(red.v == Catch::Approx(1.0f));
    CHECK(red.a == Catch::Approx(1.0f));

    hsva_t green = rgb_to_hsv(rgb_t(0, 255, 0));
    CHECK(green.h == Catch::Approx(120.0f));

    hsva_t blue = rgb_to_hsv(rgb_t(0, 0, 255));
    CHECK(blue.h == Catch::Approx(240.0f));
}

TEST_CASE("rgb_to_hsv: grays have s=0 and h=0", "[colors][rgb_to_hsv]") {
    hsva_t black = rgb_to_hsv(rgb_t(0, 0, 0));
    CHECK(black.h == 0.0f);
    CHECK(black.s == 0.0f);
    CHECK(black.v == 0.0f);

    hsva_t white = rgb_to_hsv(rgb_t(255, 255, 255));
    CHECK(white.h == 0.0f);
    CHECK(white.s == 0.0f);
    CHECK(white.v == Catch::Approx(1.0f));
}

TEST_CASE("rgb_to_hsv: hue stays in [0, 360)", "[colors][rgb_to_hsv]") {
    // Magenta-ish input exercises the negative-hue branch (max == r, g < b).
    hsva_t c = rgb_to_hsv(rgb_t(200, 10, 150));
    CHECK(c.h >= 0.0f);
    CHECK(c.h < 360.0f);
    CHECK(c.h == Catch::Approx(315.789f).epsilon(1e-3));
}

TEST_CASE("rgb_to_hsv: round-trips through hsv_to_rgb", "[colors][rgb_to_hsv]") {
    const rgb_t samples[] = {
        rgb_t(2, 6, 10), rgb_t(20, 96, 80), rgb_t(16, 64, 191),
        rgb_t(255, 128, 0), rgb_t(7, 5, 2),
    };
    for (const rgb_t& in : samples) {
        hsva_t hsv = rgb_to_hsv(in);
        rgb_t out = hsv_to_rgb(hsv.h, hsv.s, hsv.v);
        // 8-bit quantization allows off-by-one.
        CHECK(std::abs(int(out.r) - int(in.r)) <= 1);
        CHECK(std::abs(int(out.g) - int(in.g)) <= 1);
        CHECK(std::abs(int(out.b) - int(in.b)) <= 1);
    }
}
