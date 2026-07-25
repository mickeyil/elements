#include <catch2/catch_test_macros.hpp>

#include "core/fx_math.h"

// The fx helpers promise FastLED's exact integer results (fixed-scale
// build), so these tests pin known values from the originals.

TEST_CASE("scale8 matches FastLED fixed-scale math", "[fx]") {
    CHECK(fx::scale8(255, 255) == 255);
    CHECK(fx::scale8(255, 0) == 0);
    CHECK(fx::scale8(128, 255) == 128);
    CHECK(fx::scale8(200, 128) == 100);   // 200*129/256
    CHECK(fx::scale8(255, 85) == 85);
}

TEST_CASE("scale16 matches FastLED fixed-scale math", "[fx]") {
    CHECK(fx::scale16(65535, 65535) == 65535);
    CHECK(fx::scale16(65535, 0) == 0);
    CHECK(fx::scale16(32768, 90) == 45);  // 32768*91/65536
    CHECK(fx::scale16(32768, 240) == 120);
}

TEST_CASE("qadd8 saturates at 255", "[fx]") {
    CHECK(fx::qadd8(100, 55) == 155);
    CHECK(fx::qadd8(200, 100) == 255);
    CHECK(fx::qadd8(255, 255) == 255);
    CHECK(fx::qadd8(0, 0) == 0);
}

TEST_CASE("sin16 quarter points", "[fx]") {
    CHECK(fx::sin16(0) == 0);
    CHECK(fx::sin16(16384) == 32645);    // FastLED's approximated peak
    CHECK(fx::sin16(32768) == 0);
    CHECK(fx::sin16(49152) == -32645);
    CHECK(fx::sin16(8192) == 23170);     // sin(45deg) * 32767
}

TEST_CASE("sin8 quarter points", "[fx]") {
    CHECK(fx::sin8(0) == 128);
    CHECK(fx::sin8(64) == 255);
    CHECK(fx::sin8(128) == 128);
    CHECK(fx::sin8(192) == 1);
}

TEST_CASE("beat88/beat16/beat8 are pure sawtooth functions of ms", "[fx]") {
    CHECK(fx::beat88(1011, 0) == 0);
    CHECK(fx::beat16(3, 0) == 0);
    CHECK(fx::beat8(7, 0) == 0);

    // Formula check against independent 32-bit wrap-around arithmetic.
    const uint32_t ms = 123456;
    const uint32_t product16 = ms * uint32_t(1011) * 280u;
    CHECK(fx::beat88(1011, ms) == uint16_t(product16 >> 16));

    // bpm < 256 is promoted to Q8.8.
    CHECK(fx::beat16(3, ms) == fx::beat88(3 << 8, ms));
    CHECK(fx::beat8(7, ms) == uint8_t(fx::beat16(7, ms) >> 8));
}

TEST_CASE("beatsin* start at the range midpoint", "[fx]") {
    // At ms=0 the sine is at zero crossing: lowest + range/2 (integer floor).
    CHECK(fx::beatsin16(3, 179, 269, 0) == 224);
    CHECK(fx::beatsin88(1011, 10, 13, 0) == 12);
    CHECK(fx::beatsin8(9, 55, 65, 0) == 60);
}

TEST_CASE("beatsin16 stays inside its range", "[fx]") {
    for (uint32_t ms = 0; ms < 30000; ms += 91) {
        const uint16_t v = fx::beatsin16(3, 179, 269, ms);
        CHECK(v >= 179);
        CHECK(v <= 269);
    }
}

TEST_CASE("average_light matches CRGB::getAverageLight", "[fx]") {
    CHECK(fx::average_light(rgb_t(255, 255, 255)) == 255);
    CHECK(fx::average_light(rgb_t(0, 0, 0)) == 0);
    CHECK(fx::average_light(rgb_t(100, 100, 100)) == 99);  // 3 * scale8(100,85)
}

TEST_CASE("color_from_palette blends and wraps", "[fx]") {
    fx::palette16 pal = {};
    pal.entries[0] = rgb_t(0, 0, 0);
    pal.entries[1] = rgb_t(16, 32, 64);
    pal.entries[15] = rgb_t(100, 0, 0);

    SECTION("exact entry, full brightness") {
        const rgb_t c = fx::color_from_palette(pal, 16, 255);
        CHECK(c.r == 16);
        CHECK(c.g == 32);
        CHECK(c.b == 64);
    }
    SECTION("midpoint blend between entries 0 and 1") {
        const rgb_t c = fx::color_from_palette(pal, 8, 255);
        CHECK(c.r == 8);
        CHECK(c.g == 16);
        CHECK(c.b == 32);
    }
    SECTION("entry 15 blends toward entry 0") {
        const rgb_t c = fx::color_from_palette(pal, 248, 255);
        CHECK(c.r == 50);  // scale8(100,127) + scale8(0,128)
        CHECK(c.g == 0);
        CHECK(c.b == 0);
    }
    SECTION("brightness scales the result") {
        const rgb_t c = fx::color_from_palette(pal, 16, 127);
        CHECK(c.r == 8);
        CHECK(c.g == 16);
        CHECK(c.b == 32);
    }
    SECTION("brightness 0 is black") {
        const rgb_t c = fx::color_from_palette(pal, 16, 0);
        CHECK(c.r == 0);
        CHECK(c.g == 0);
        CHECK(c.b == 0);
    }
}
