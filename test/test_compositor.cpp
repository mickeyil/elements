#include <catch2/catch_test_macros.hpp>

#include <cstdint>

#include "core/colors.h"
#include "core/compositor.h"
#include "core/pixel_view.h"
#include "core/strip.h"

namespace {

hsva_t hsva(float h, float s, float v, float a = 1.0f) {
    return hsva_t(h, s, v, a);
}

void init_identity(PixelView& v, hsva_t* buf, uint16_t size) {
    REQUIRE(v.initialize(buf, size,
                         /*storage_indices*/ nullptr,
                         /*has_physical_mapping*/ true,
                         /*physical_indices*/ nullptr,
                         /*physical_identity*/ true));
}

void init_phys(PixelView& v, hsva_t* buf, uint16_t size, const uint16_t* phys) {
    REQUIRE(v.initialize(buf, size,
                         /*storage_indices*/ nullptr,
                         /*has_physical_mapping*/ true,
                         /*physical_indices*/ phys,
                         /*physical_identity*/ false));
}

void check_pixel(const Strip& s, uint16_t i, const rgb_t& expected) {
    CHECK(s[i].r == expected.r);
    CHECK(s[i].g == expected.g);
    CHECK(s[i].b == expected.b);
}

void check_black(const Strip& s, uint16_t i) {
    check_pixel(s, i, rgb_t(0, 0, 0));
}

}  // namespace

// ---------------------------------------------------------------------------
// Empty / inactive
// ---------------------------------------------------------------------------

TEST_CASE("Compositor: zero-count composite clears the strip", "[compositor]") {
    Strip strip;
    REQUIRE(strip.resize(4));
    strip[0] = rgb_t(99, 99, 99);
    strip[3] = rgb_t(11, 22, 33);

    Compositor c;
    c.composite(strip, nullptr, 0);

    for (uint16_t i = 0; i < 4; i++) check_black(strip, i);
}

TEST_CASE("Compositor: nullptr layer entry is skipped", "[compositor]") {
    Strip strip;
    REQUIRE(strip.resize(2));
    PixelView* views[] = { nullptr };

    Compositor c;
    c.composite(strip, views, 1);

    check_black(strip, 0);
    check_black(strip, 1);
}

// ---------------------------------------------------------------------------
// Single layer
// ---------------------------------------------------------------------------

TEST_CASE("Compositor: opaque view writes hsv_to_rgb at each pixel", "[compositor]") {
    Strip strip;
    REQUIRE(strip.resize(3));
    hsva_t buf[3] = {
        hsva(0.0f,   1.0f, 1.0f),  // red
        hsva(120.0f, 1.0f, 1.0f),  // green
        hsva(240.0f, 1.0f, 1.0f),  // blue
    };
    PixelView v;
    init_identity(v, buf, 3);
    PixelView* views[] = { &v };

    Compositor c;
    c.composite(strip, views, 1);

    check_pixel(strip, 0, hsv_to_rgb(0.0f,   1.0f, 1.0f));
    check_pixel(strip, 1, hsv_to_rgb(120.0f, 1.0f, 1.0f));
    check_pixel(strip, 2, hsv_to_rgb(240.0f, 1.0f, 1.0f));
}

TEST_CASE("Compositor: transparent pixel (a == 0) leaves output untouched", "[compositor]") {
    Strip strip;
    REQUIRE(strip.resize(2));
    hsva_t buf[2] = {
        hsva(0.0f,   1.0f, 1.0f, 0.0f),  // transparent
        hsva(120.0f, 1.0f, 1.0f, 1.0f),  // opaque green
    };
    PixelView v;
    init_identity(v, buf, 2);
    PixelView* views[] = { &v };

    Compositor c;
    c.composite(strip, views, 1);

    check_black(strip, 0);
    check_pixel(strip, 1, hsv_to_rgb(120.0f, 1.0f, 1.0f));
}

TEST_CASE("Compositor: partial alpha blends with cleared base", "[compositor]") {
    Strip strip;
    REQUIRE(strip.resize(1));
    hsva_t buf[1] = { hsva(0.0f, 1.0f, 1.0f, 0.5f) };  // half-red over black
    PixelView v;
    init_identity(v, buf, 1);
    PixelView* views[] = { &v };

    Compositor c;
    c.composite(strip, views, 1);

    const rgb_t fg = hsv_to_rgb(0.0f, 1.0f, 1.0f);
    check_pixel(strip, 0, rgb_alpha_blend(rgb_t(0, 0, 0), fg, 0.5f));
}

// ---------------------------------------------------------------------------
// Multi-layer ordering
// ---------------------------------------------------------------------------

TEST_CASE("Compositor: opaque top layer overwrites bottom layer", "[compositor]") {
    Strip strip;
    REQUIRE(strip.resize(2));
    hsva_t bot_buf[2] = { hsva(0.0f, 1.0f, 1.0f), hsva(120.0f, 1.0f, 1.0f) };
    hsva_t top_buf[2] = { hsva(240.0f, 1.0f, 1.0f), hsva(60.0f, 1.0f, 1.0f) };
    PixelView bot, top;
    init_identity(bot, bot_buf, 2);
    init_identity(top, top_buf, 2);
    PixelView* views[] = { &bot, &top };

    Compositor c;
    c.composite(strip, views, 2);

    check_pixel(strip, 0, hsv_to_rgb(240.0f, 1.0f, 1.0f));
    check_pixel(strip, 1, hsv_to_rgb(60.0f,  1.0f, 1.0f));
}

TEST_CASE("Compositor: semi-transparent top layer blends with bottom", "[compositor]") {
    Strip strip;
    REQUIRE(strip.resize(1));
    hsva_t bot_buf[1] = { hsva(0.0f,   1.0f, 1.0f, 1.0f) };  // opaque red
    hsva_t top_buf[1] = { hsva(240.0f, 1.0f, 1.0f, 0.5f) };  // half blue
    PixelView bot, top;
    init_identity(bot, bot_buf, 1);
    init_identity(top, top_buf, 1);
    PixelView* views[] = { &bot, &top };

    Compositor c;
    c.composite(strip, views, 2);

    const rgb_t bg = hsv_to_rgb(0.0f,   1.0f, 1.0f);
    const rgb_t fg = hsv_to_rgb(240.0f, 1.0f, 1.0f);
    check_pixel(strip, 0, rgb_alpha_blend(bg, fg, 0.5f));
}

TEST_CASE("Compositor: mixed active/inactive layers - only active contribute", "[compositor]") {
    Strip strip;
    REQUIRE(strip.resize(2));
    hsva_t buf[2] = { hsva(120.0f, 1.0f, 1.0f), hsva(120.0f, 1.0f, 1.0f) };
    PixelView v;
    init_identity(v, buf, 2);
    PixelView* views[] = { nullptr, &v, nullptr };

    Compositor c;
    c.composite(strip, views, 3);

    const rgb_t green = hsv_to_rgb(120.0f, 1.0f, 1.0f);
    check_pixel(strip, 0, green);
    check_pixel(strip, 1, green);
}

// ---------------------------------------------------------------------------
// Physical mapping
// ---------------------------------------------------------------------------

TEST_CASE("Compositor: non-identity physical mapping routes view[i] to strip[phys[i]]",
          "[compositor]") {
    Strip strip;
    REQUIRE(strip.resize(5));
    hsva_t buf[3] = {
        hsva(0.0f,   1.0f, 1.0f),
        hsva(120.0f, 1.0f, 1.0f),
        hsva(240.0f, 1.0f, 1.0f),
    };
    const uint16_t phys[3] = { 4, 0, 2 };
    PixelView v;
    init_phys(v, buf, 3, phys);
    PixelView* views[] = { &v };

    Compositor c;
    c.composite(strip, views, 1);

    check_pixel(strip, 0, hsv_to_rgb(120.0f, 1.0f, 1.0f));  // view[1] -> 0
    check_black(strip, 1);
    check_pixel(strip, 2, hsv_to_rgb(240.0f, 1.0f, 1.0f));  // view[2] -> 2
    check_black(strip, 3);
    check_pixel(strip, 4, hsv_to_rgb(0.0f,   1.0f, 1.0f));  // view[0] -> 4
}

TEST_CASE("Compositor: view shorter than strip leaves uncovered pixels black",
          "[compositor]") {
    Strip strip;
    REQUIRE(strip.resize(5));
    hsva_t buf[2] = { hsva(0.0f, 1.0f, 1.0f), hsva(120.0f, 1.0f, 1.0f) };
    const uint16_t phys[2] = { 0, 1 };
    PixelView v;
    init_phys(v, buf, 2, phys);
    PixelView* views[] = { &v };

    Compositor c;
    c.composite(strip, views, 1);

    check_pixel(strip, 0, hsv_to_rgb(0.0f,   1.0f, 1.0f));
    check_pixel(strip, 1, hsv_to_rgb(120.0f, 1.0f, 1.0f));
    check_black(strip, 2);
    check_black(strip, 3);
    check_black(strip, 4);
}

// ---------------------------------------------------------------------------
// Per-frame clear
// ---------------------------------------------------------------------------

TEST_CASE("Compositor: each composite clears the strip first", "[compositor]") {
    Strip strip;
    REQUIRE(strip.resize(2));
    hsva_t buf[2] = { hsva(0.0f, 1.0f, 1.0f), hsva(120.0f, 1.0f, 1.0f) };
    PixelView v;
    init_identity(v, buf, 2);
    PixelView* active[] = { &v };
    PixelView* none[]   = { nullptr };

    Compositor c;
    c.composite(strip, active, 1);
    c.composite(strip, none, 1);  // no contributors this frame

    check_black(strip, 0);
    check_black(strip, 1);
}
