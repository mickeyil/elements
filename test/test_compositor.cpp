#include <catch2/catch_test_macros.hpp>
#include <catch2/catch_approx.hpp>
#include "../src/compositor.h"
#include "../src/strip.h"
#include "../src/colors.h"
#include "../src/decoder.h"

#include <cstring>

static const uint16_t STRIP_LEN = 4;

struct TestStrip {
    uint8_t rgb[STRIP_LEN * 3];
    Strip strip;

    TestStrip() : strip(rgb, STRIP_LEN) {
        memset(rgb, 0, sizeof(rgb));
    }
};

// Hand-built layer with identity index map
struct TestLayer {
    uint8_t index_map[STRIP_LEN];
    hsva_t buffer[STRIP_LEN];
    LayerDef def;

    TestLayer() {
        for (uint8_t i = 0; i < STRIP_LEN; i++) index_map[i] = i;
        memset(buffer, 0, sizeof(buffer));
        def.index_map_length = STRIP_LEN;
        def.index_map = index_map;
        def.event_count = 0;
        def.events = nullptr;
        def.buffer = buffer;
    }
};

// ---------------------------------------------------------------------------
// Alpha blending
// ---------------------------------------------------------------------------

TEST_CASE("Compositor: single layer A=1.0 produces HSV->RGB", "[compositor]") {
    TestStrip ts;
    TestLayer layer;
    for (uint8_t i = 0; i < STRIP_LEN; i++)
        layer.buffer[i] = hsva_t(0.0f, 1.0f, 1.0f, 1.0f);

    Compositor comp(ts.strip, false);
    comp.composite(&layer.def, 1, 0x01);

    rgb_t expected = hsv_to_rgb(0.0f, 1.0f, 1.0f);
    for (uint8_t i = 0; i < STRIP_LEN; i++) {
        rgb_t c = ts.strip.get_rgb(i);
        CHECK(c.r == expected.r);
        CHECK(c.g == expected.g);
        CHECK(c.b == expected.b);
    }
}

TEST_CASE("Compositor: single layer A=0.0 stays black", "[compositor]") {
    TestStrip ts;
    TestLayer layer;
    for (uint8_t i = 0; i < STRIP_LEN; i++)
        layer.buffer[i] = hsva_t(0.0f, 1.0f, 1.0f, 0.0f);

    Compositor comp(ts.strip, false);
    comp.composite(&layer.def, 1, 0x01);

    for (uint8_t i = 0; i < STRIP_LEN; i++) {
        rgb_t c = ts.strip.get_rgb(i);
        CHECK(c.r == 0);
        CHECK(c.g == 0);
        CHECK(c.b == 0);
    }
}

TEST_CASE("Compositor: single layer A=0.5 blends with black", "[compositor]") {
    TestStrip ts;
    TestLayer layer;
    for (uint8_t i = 0; i < STRIP_LEN; i++)
        layer.buffer[i] = hsva_t(0.0f, 1.0f, 1.0f, 0.5f);

    Compositor comp(ts.strip, false);
    comp.composite(&layer.def, 1, 0x01);

    rgb_t red = hsv_to_rgb(0.0f, 1.0f, 1.0f);
    rgb_t expected = rgb_lerp(rgb_t(0, 0, 0), red, 0.5f);
    for (uint8_t i = 0; i < STRIP_LEN; i++) {
        rgb_t c = ts.strip.get_rgb(i);
        CHECK(c.r == expected.r);
        CHECK(c.g == expected.g);
        CHECK(c.b == expected.b);
    }
}

// ---------------------------------------------------------------------------
// Multi-layer compositing order
// ---------------------------------------------------------------------------

TEST_CASE("Compositor: two layers A=1.0, top layer wins", "[compositor]") {
    TestStrip ts;
    TestLayer bottom, top;

    for (uint8_t i = 0; i < STRIP_LEN; i++)
        bottom.buffer[i] = hsva_t(0.0f, 1.0f, 1.0f, 1.0f);   // red
    for (uint8_t i = 0; i < STRIP_LEN; i++)
        top.buffer[i] = hsva_t(240.0f, 1.0f, 1.0f, 1.0f);     // blue

    LayerDef defs[2] = { bottom.def, top.def };
    Compositor comp(ts.strip, false);
    comp.composite(defs, 2, 0x03);

    rgb_t expected = hsv_to_rgb(240.0f, 1.0f, 1.0f);
    for (uint8_t i = 0; i < STRIP_LEN; i++) {
        rgb_t c = ts.strip.get_rgb(i);
        CHECK(c.r == expected.r);
        CHECK(c.g == expected.g);
        CHECK(c.b == expected.b);
    }
}

TEST_CASE("Compositor: bottom A=1.0, top A=0.5 blends", "[compositor]") {
    TestStrip ts;
    TestLayer bottom, top;

    for (uint8_t i = 0; i < STRIP_LEN; i++)
        bottom.buffer[i] = hsva_t(0.0f, 1.0f, 1.0f, 1.0f);   // red
    for (uint8_t i = 0; i < STRIP_LEN; i++)
        top.buffer[i] = hsva_t(240.0f, 1.0f, 1.0f, 0.5f);     // blue 50%

    LayerDef defs[2] = { bottom.def, top.def };
    Compositor comp(ts.strip, false);
    comp.composite(defs, 2, 0x03);

    rgb_t red = hsv_to_rgb(0.0f, 1.0f, 1.0f);
    rgb_t blue = hsv_to_rgb(240.0f, 1.0f, 1.0f);
    rgb_t expected = rgb_lerp(red, blue, 0.5f);
    for (uint8_t i = 0; i < STRIP_LEN; i++) {
        rgb_t c = ts.strip.get_rgb(i);
        CHECK(c.r == expected.r);
        CHECK(c.g == expected.g);
        CHECK(c.b == expected.b);
    }
}

// ---------------------------------------------------------------------------
// Gamma on vs off
// ---------------------------------------------------------------------------

TEST_CASE("Compositor: gamma on vs off differ", "[compositor][gamma]") {
    TestLayer layer;
    for (uint8_t i = 0; i < STRIP_LEN; i++)
        layer.buffer[i] = hsva_t(0.0f, 1.0f, 0.5f, 1.0f);

    TestStrip ts_off;
    Compositor comp_off(ts_off.strip, false);
    comp_off.composite(&layer.def, 1, 0x01);

    TestStrip ts_on;
    Compositor comp_on(ts_on.strip, true);
    comp_on.composite(&layer.def, 1, 0x01);

    bool differs = false;
    for (uint16_t i = 0; i < STRIP_LEN * 3; i++) {
        if (ts_off.rgb[i] != ts_on.rgb[i]) { differs = true; break; }
    }
    CHECK(differs);
}

TEST_CASE("Compositor: gamma off matches linear HSV->RGB", "[compositor][gamma]") {
    TestStrip ts;
    TestLayer layer;
    for (uint8_t i = 0; i < STRIP_LEN; i++)
        layer.buffer[i] = hsva_t(120.0f, 1.0f, 0.8f, 1.0f);

    Compositor comp(ts.strip, false);
    comp.composite(&layer.def, 1, 0x01);

    rgb_t expected = hsv_to_rgb(120.0f, 1.0f, 0.8f);
    for (uint8_t i = 0; i < STRIP_LEN; i++) {
        rgb_t c = ts.strip.get_rgb(i);
        CHECK(c.r == expected.r);
        CHECK(c.g == expected.g);
        CHECK(c.b == expected.b);
    }
}

TEST_CASE("Compositor: gamma on matches gamma_correct(linear)", "[compositor][gamma]") {
    TestStrip ts;
    TestLayer layer;
    for (uint8_t i = 0; i < STRIP_LEN; i++)
        layer.buffer[i] = hsva_t(120.0f, 1.0f, 0.8f, 1.0f);

    Compositor comp(ts.strip, true);
    comp.composite(&layer.def, 1, 0x01);

    rgb_t linear = hsv_to_rgb(120.0f, 1.0f, 0.8f);
    rgb_t expected = gamma_correct(linear);
    for (uint8_t i = 0; i < STRIP_LEN; i++) {
        rgb_t c = ts.strip.get_rgb(i);
        CHECK(c.r == expected.r);
        CHECK(c.g == expected.g);
        CHECK(c.b == expected.b);
    }
}

// ---------------------------------------------------------------------------
// Empty active_mask
// ---------------------------------------------------------------------------

TEST_CASE("Compositor: active_mask=0 produces all black", "[compositor]") {
    TestStrip ts;
    TestLayer layer;
    for (uint8_t i = 0; i < STRIP_LEN; i++)
        layer.buffer[i] = hsva_t(0.0f, 1.0f, 1.0f, 1.0f);

    // Pre-fill strip with non-zero to verify clear
    for (uint16_t i = 0; i < STRIP_LEN * 3; i++) ts.rgb[i] = 0xFF;

    Compositor comp(ts.strip, false);
    comp.composite(&layer.def, 1, 0x00);

    for (uint16_t i = 0; i < STRIP_LEN * 3; i++) {
        CHECK(ts.rgb[i] == 0);
    }
}
