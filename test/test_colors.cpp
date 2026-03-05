#include <catch2/catch_test_macros.hpp>
#include <catch2/catch_approx.hpp>
#include "../src/colors.h"

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
// gamma_correct
// ---------------------------------------------------------------------------

TEST_CASE("gamma_correct: 0 maps to 0", "[colors][gamma]") {
    rgb_t c = gamma_correct(rgb_t(0, 0, 0));
    CHECK(c.r == 0);
    CHECK(c.g == 0);
    CHECK(c.b == 0);
}

TEST_CASE("gamma_correct: 255 maps to 255", "[colors][gamma]") {
    rgb_t c = gamma_correct(rgb_t(255, 255, 255));
    CHECK(c.r == 255);
    CHECK(c.g == 255);
    CHECK(c.b == 255);
}

TEST_CASE("gamma_correct: midpoint is darker", "[colors][gamma]") {
    rgb_t c = gamma_correct(rgb_t(128, 128, 128));
    CHECK(c.r < 128);
    CHECK(c.r > 0);
}

TEST_CASE("gamma_correct: monotonically increasing", "[colors][gamma]") {
    for (int n = 1; n < 256; n++) {
        rgb_t a = gamma_correct(rgb_t(n - 1, 0, 0));
        rgb_t b = gamma_correct(rgb_t(n, 0, 0));
        CHECK(b.r >= a.r);
    }
}

// ---------------------------------------------------------------------------
// rgb_lerp
// ---------------------------------------------------------------------------

TEST_CASE("rgb_lerp: t=0 returns a", "[colors][lerp]") {
    rgb_t a(100, 150, 200);
    rgb_t b(200, 50, 10);
    rgb_t c = rgb_lerp(a, b, 0.0f);
    CHECK(c.r == a.r);
    CHECK(c.g == a.g);
    CHECK(c.b == a.b);
}

TEST_CASE("rgb_lerp: t=1 returns b", "[colors][lerp]") {
    rgb_t a(100, 150, 200);
    rgb_t b(200, 50, 10);
    rgb_t c = rgb_lerp(a, b, 1.0f);
    CHECK(c.r == b.r);
    CHECK(c.g == b.g);
    CHECK(c.b == b.b);
}

TEST_CASE("rgb_lerp: t=0.5 midpoint", "[colors][lerp]") {
    rgb_t a(0, 0, 0);
    rgb_t b(200, 100, 50);
    rgb_t c = rgb_lerp(a, b, 0.5f);
    CHECK(c.r == 100);
    CHECK(c.g == 50);
    CHECK(c.b == 25);
}

TEST_CASE("rgb_lerp: t<0 clamps to a", "[colors][lerp]") {
    rgb_t a(100, 150, 200);
    rgb_t b(200, 50, 10);
    rgb_t c = rgb_lerp(a, b, -1.0f);
    CHECK(c.r == a.r);
    CHECK(c.g == a.g);
    CHECK(c.b == a.b);
}

TEST_CASE("rgb_lerp: t>1 clamps to b", "[colors][lerp]") {
    rgb_t a(100, 150, 200);
    rgb_t b(200, 50, 10);
    rgb_t c = rgb_lerp(a, b, 2.0f);
    CHECK(c.r == b.r);
    CHECK(c.g == b.g);
    CHECK(c.b == b.b);
}
