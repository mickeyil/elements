#include <catch2/catch_test_macros.hpp>

#include <cstdint>
#include <cstring>

#include "core/colors.h"
#include "core/gamma.h"
#include "core/hardware_profile.h"
#include "core/strip.h"

namespace {
rgb_t pix(uint8_t r, uint8_t g, uint8_t b) { return rgb_t(r, g, b); }
const GammaCorrection IDENTITY;  // default-constructed LUT passes bytes through
}  // namespace

// ---------------------------------------------------------------------------
// Default / empty
// ---------------------------------------------------------------------------

TEST_CASE("Strip: default-constructed is empty", "[strip]") {
    Strip s;
    CHECK(s.size() == 0);
    CHECK(s.empty());
    CHECK(s.byte_size() == 0);
    CHECK(s.pixels() == nullptr);
    CHECK(s.bytes() == nullptr);
}

// ---------------------------------------------------------------------------
// resize / reset
// ---------------------------------------------------------------------------

TEST_CASE("Strip: resize allocates and zeroes", "[strip]") {
    Strip s;
    REQUIRE(s.resize(4));

    CHECK(s.size() == 4);
    CHECK_FALSE(s.empty());
    CHECK(s.byte_size() == 4 * sizeof(rgb_t));
    REQUIRE(s.pixels() != nullptr);
    CHECK(reinterpret_cast<const uint8_t*>(s.pixels()) == s.bytes());

    for (uint16_t i = 0; i < 4; i++) {
        CHECK(s[i].r == 0);
        CHECK(s[i].g == 0);
        CHECK(s[i].b == 0);
    }
}

TEST_CASE("Strip: write-then-read round-trip via operator[]", "[strip]") {
    Strip s;
    REQUIRE(s.resize(3));

    s[0] = pix(10, 20, 30);
    s[1] = pix(40, 50, 60);
    s[2] = pix(70, 80, 90);

    CHECK(s[0].r == 10);
    CHECK(s[0].g == 20);
    CHECK(s[0].b == 30);
    CHECK(s[2].r == 70);
    CHECK(s[2].g == 80);
    CHECK(s[2].b == 90);

    // bytes() aliases pixels() at byte granularity.
    const uint8_t* b = s.bytes();
    CHECK(b[0] == 10);
    CHECK(b[1] == 20);
    CHECK(b[2] == 30);
    CHECK(b[3] == 40);
}

TEST_CASE("Strip: resize(0) leaves the strip empty", "[strip]") {
    Strip s;
    REQUIRE(s.resize(0));

    CHECK(s.empty());
    CHECK(s.size() == 0);
    CHECK(s.pixels() == nullptr);
}

TEST_CASE("Strip: re-resize replaces and zeroes new contents", "[strip]") {
    Strip s;
    REQUIRE(s.resize(4));
    s[0] = pix(1, 2, 3);
    s[3] = pix(4, 5, 6);

    REQUIRE(s.resize(2));  // shrink

    REQUIRE(s.size() == 2);
    CHECK(s[0].r == 0);
    CHECK(s[1].r == 0);

    REQUIRE(s.resize(6));  // grow

    REQUIRE(s.size() == 6);
    for (uint16_t i = 0; i < 6; i++) {
        CHECK(s[i].r == 0);
        CHECK(s[i].g == 0);
        CHECK(s[i].b == 0);
    }
}

TEST_CASE("Strip: reset returns to empty", "[strip]") {
    Strip s;
    REQUIRE(s.resize(3));

    s.reset();

    CHECK(s.empty());
    CHECK(s.size() == 0);
    CHECK(s.pixels() == nullptr);
    CHECK(s.bytes() == nullptr);
}

// ---------------------------------------------------------------------------
// clear
// ---------------------------------------------------------------------------

TEST_CASE("Strip: clear zeroes every pixel", "[strip]") {
    Strip s;
    REQUIRE(s.resize(3));
    s[0] = pix(1, 2, 3);
    s[1] = pix(4, 5, 6);
    s[2] = pix(7, 8, 9);

    s.clear();

    for (uint16_t i = 0; i < 3; i++) {
        CHECK(s[i].r == 0);
        CHECK(s[i].g == 0);
        CHECK(s[i].b == 0);
    }
}

TEST_CASE("Strip: clear on empty strip is a no-op", "[strip]") {
    Strip s;
    s.clear();  // must not crash
    CHECK(s.empty());
}

// ---------------------------------------------------------------------------
// copy_to: gamma
// ---------------------------------------------------------------------------

TEST_CASE("Strip: copy_to applies the gamma LUT to every channel", "[strip][copy_to][gamma]") {
    Strip s;
    REQUIRE(s.resize(3));
    const rgb_t input[] = { pix(10, 50, 200), pix(0, 128, 255), pix(33, 77, 144) };
    for (uint16_t i = 0; i < 3; i++) s[i] = input[i];

    GammaCorrection g;
    g.set_gamma(2.0f);

    uint8_t dst[9] = {};
    s.copy_to(dst, 3, ColorOrder::RGB, g);

    for (uint16_t i = 0; i < 3; i++) {
        const rgb_t expected = g.correct(input[i]);
        CHECK(dst[i * 3 + 0] == expected.r);
        CHECK(dst[i * 3 + 1] == expected.g);
        CHECK(dst[i * 3 + 2] == expected.b);
    }
    // Gamma is applied on the way out; the strip keeps program-space values.
    CHECK(s[0].r == 10);
    CHECK(s[0].g == 50);
    CHECK(s[0].b == 200);
}

TEST_CASE("Strip: copy_to applies gamma before channel order", "[strip][copy_to][gamma]") {
    Strip s;
    REQUIRE(s.resize(1));
    s[0] = pix(10, 128, 200);

    GammaCorrection g;
    g.set_gamma(DEFAULT_GAMMA);
    const rgb_t expected = g.correct(s[0]);

    uint8_t dst[3] = {};
    s.copy_to(dst, 1, ColorOrder::BGR, g);

    CHECK(dst[0] == expected.b);
    CHECK(dst[1] == expected.g);
    CHECK(dst[2] == expected.r);
}

TEST_CASE("Strip: copy_to fills a full LED buffer the way the firmware writes it",
          "[strip][copy_to][gamma]") {
    // EspFrameOutput::write() copies into a MAX_STRIP_PIXELS buffer with the
    // profile's gamma and order; LEDs past the strip must go black.
    Strip s;
    REQUIRE(s.resize(2));
    s[0] = pix(255, 128, 0);
    s[1] = pix(0, 64, 255);

    GammaCorrection g;
    g.set_gamma(DEFAULT_GAMMA);

    uint8_t leds[MAX_STRIP_PIXELS * 3];
    std::memset(leds, 0xAA, sizeof(leds));
    s.copy_to(leds, MAX_STRIP_PIXELS, ColorOrder::BGR, g);

    const rgb_t p0 = g.correct(s[0]);
    const rgb_t p1 = g.correct(s[1]);
    CHECK(leds[0] == p0.b); CHECK(leds[1] == p0.g); CHECK(leds[2] == p0.r);
    CHECK(leds[3] == p1.b); CHECK(leds[4] == p1.g); CHECK(leds[5] == p1.r);

    bool tail_black = true;
    for (size_t i = 6; i < sizeof(leds); i++) {
        if (leds[i] != 0) tail_black = false;
    }
    CHECK(tail_black);
}

// ---------------------------------------------------------------------------
// copy_to
// ---------------------------------------------------------------------------

TEST_CASE("Strip: copy_to RGB lays out r,g,b per pixel", "[strip][copy_to]") {
    Strip s;
    REQUIRE(s.resize(2));
    s[0] = pix(10, 20, 30);
    s[1] = pix(40, 50, 60);

    uint8_t dst[6] = {};
    s.copy_to(dst, 2, ColorOrder::RGB, IDENTITY);

    CHECK(dst[0] == 10); CHECK(dst[1] == 20); CHECK(dst[2] == 30);
    CHECK(dst[3] == 40); CHECK(dst[4] == 50); CHECK(dst[5] == 60);
}

TEST_CASE("Strip: copy_to BGR lays out b,g,r per pixel", "[strip][copy_to]") {
    Strip s;
    REQUIRE(s.resize(2));
    s[0] = pix(10, 20, 30);
    s[1] = pix(40, 50, 60);

    uint8_t dst[6] = {};
    s.copy_to(dst, 2, ColorOrder::BGR, IDENTITY);

    CHECK(dst[0] == 30); CHECK(dst[1] == 20); CHECK(dst[2] == 10);
    CHECK(dst[3] == 60); CHECK(dst[4] == 50); CHECK(dst[5] == 40);
}

TEST_CASE("Strip: copy_to truncates when dst_pixels < size", "[strip][copy_to]") {
    Strip s;
    REQUIRE(s.resize(4));
    s[0] = pix(1, 2, 3);
    s[1] = pix(4, 5, 6);
    s[2] = pix(7, 8, 9);
    s[3] = pix(10, 11, 12);

    uint8_t dst[12];
    std::memset(dst, 0xAA, sizeof(dst));  // sentinel

    s.copy_to(dst, 2, ColorOrder::RGB, IDENTITY);

    CHECK(dst[0] == 1); CHECK(dst[1] == 2); CHECK(dst[2] == 3);
    CHECK(dst[3] == 4); CHECK(dst[4] == 5); CHECK(dst[5] == 6);
    // Bytes past dst_pixels * 3 are untouched.
    for (size_t i = 6; i < sizeof(dst); i++) {
        CHECK(dst[i] == 0xAA);
    }
}

TEST_CASE("Strip: copy_to zero-pads when dst_pixels > size", "[strip][copy_to]") {
    Strip s;
    REQUIRE(s.resize(2));
    s[0] = pix(1, 2, 3);
    s[1] = pix(4, 5, 6);

    uint8_t dst[15];
    std::memset(dst, 0xAA, sizeof(dst));  // sentinel

    s.copy_to(dst, 5, ColorOrder::RGB, IDENTITY);

    CHECK(dst[0] == 1); CHECK(dst[1] == 2); CHECK(dst[2] == 3);
    CHECK(dst[3] == 4); CHECK(dst[4] == 5); CHECK(dst[5] == 6);
    // Tail of 3 pixels (9 bytes) is zeroed.
    for (size_t i = 6; i < 15; i++) {
        CHECK(dst[i] == 0);
    }
}

TEST_CASE("Strip: copy_to on empty strip zeroes the destination", "[strip][copy_to]") {
    Strip s;  // never resized

    uint8_t dst[9];
    std::memset(dst, 0xAA, sizeof(dst));

    s.copy_to(dst, 3, ColorOrder::RGB, IDENTITY);

    for (size_t i = 0; i < sizeof(dst); i++) {
        CHECK(dst[i] == 0);
    }
}

TEST_CASE("Strip: copy_to with dst_pixels == 0 writes nothing", "[strip][copy_to]") {
    Strip s;
    REQUIRE(s.resize(2));
    s[0] = pix(1, 2, 3);

    uint8_t dst[6];
    std::memset(dst, 0xAA, sizeof(dst));

    s.copy_to(dst, 0, ColorOrder::RGB, IDENTITY);

    for (size_t i = 0; i < sizeof(dst); i++) {
        CHECK(dst[i] == 0xAA);
    }
}

TEST_CASE("Strip: copy_to with null dst is a no-op", "[strip][copy_to]") {
    Strip s;
    REQUIRE(s.resize(2));
    s[0] = pix(1, 2, 3);
    s.copy_to(nullptr, 2, ColorOrder::RGB, IDENTITY);  // must not crash
}
