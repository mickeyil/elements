#include <catch2/catch_test_macros.hpp>
#include <catch2/catch_approx.hpp>

#include "apps/joystick_lamp/lamp_logic.h"
#include "core/colors.h"
#include "core/pixel_view.h"

using Catch::Approx;

namespace {

constexpr uint32_t STEP_MS = 10;

// Steps the lamp in STEP_MS ticks like the ~50 fps firmware loop.
struct Rig {
    LampState lamp;

    void run(float x, float y, uint32_t total_ms) {
        for (uint32_t t = 0; t < total_ms; t += STEP_MS) {
            lamp.update(x, y, STEP_MS);
        }
    }
};

}  // namespace

TEST_CASE("deflection inside the deadzone changes nothing") {
    Rig rig;
    rig.run(0.19f, -0.19f, 2000);
    REQUIRE(rig.lamp.intensity() == 1.0f);
    REQUIRE(rig.lamp.hue_for_current() == 0.0f);
}

TEST_CASE("full horizontal deflection turns hue 360 deg in 4.5 s") {
    Rig rig;
    rig.run(1.0f, 0.0f, 2250);
    REQUIRE(rig.lamp.hue_for_current() == Approx(180.0f).margin(0.5f));
}

TEST_CASE("hue rate scales with displacement") {
    // 0.6 deflection is halfway through the active range: half the max rate.
    Rig rig;
    rig.run(0.6f, 0.0f, 2250);
    REQUIRE(rig.lamp.hue_for_current() == Approx(90.0f).margin(0.5f));
}

TEST_CASE("negative deflection wraps hue below zero") {
    Rig rig;
    rig.run(-1.0f, 0.0f, 1125);
    REQUIRE(rig.lamp.hue_for_current() == Approx(270.0f).margin(0.5f));
}

TEST_CASE("full vertical deflection sweeps intensity in 2 s and clamps") {
    Rig rig;

    rig.run(0.0f, -1.0f, 1000);
    REQUIRE(rig.lamp.intensity() == Approx(0.5f).margin(0.01f));

    rig.run(0.0f, -1.0f, 5000);
    REQUIRE(rig.lamp.intensity() == 0.0f);

    rig.run(0.0f, 1.0f, 5000);
    REQUIRE(rig.lamp.intensity() == 1.0f);
}

TEST_CASE("intensity rate scales with displacement") {
    // Half rate: 1 s at 0.6 deflection drops 0.25.
    Rig rig;
    rig.run(0.0f, -0.6f, 1000);
    REQUIRE(rig.lamp.intensity() == Approx(0.75f).margin(0.01f));
}

TEST_CASE("diagonal deflection adjusts hue and intensity together") {
    Rig rig;
    rig.run(1.0f, -1.0f, 1000);
    REQUIRE(rig.lamp.hue_for_current() == Approx(80.0f).margin(0.5f));
    REQUIRE(rig.lamp.intensity() == Approx(0.5f).margin(0.01f));
}

TEST_CASE("button cycles animations forward with wraparound") {
    Rig rig;
    REQUIRE(rig.lamp.anim_index() == 0);

    rig.lamp.next_anim();
    REQUIRE(rig.lamp.anim_index() == 1);
    rig.lamp.next_anim();
    REQUIRE(rig.lamp.anim_index() == 2);
    rig.lamp.next_anim();
    REQUIRE(rig.lamp.anim_index() == 3);
    rig.lamp.next_anim();
    REQUIRE(rig.lamp.anim_index() == 0);
}

TEST_CASE("hue offsets are kept per animation") {
    Rig rig;
    rig.run(1.0f, 0.0f, 1000);  // +80 degrees on pacifica
    const float hue0 = rig.lamp.hue_for_current();
    REQUIRE(hue0 == Approx(80.0f).margin(0.5f));

    rig.lamp.next_anim();  // red_alert
    rig.lamp.next_anim();  // police
    rig.lamp.next_anim();  // soft
    REQUIRE(rig.lamp.hue_for_current() == 0.0f);
    rig.run(-1.0f, 0.0f, 500);  // -40 degrees on soft
    REQUIRE(rig.lamp.hue_for_current() == Approx(320.0f).margin(0.5f));

    rig.lamp.next_anim();
    REQUIRE(rig.lamp.hue_for_current() == Approx(hue0));
}

TEST_CASE("hue is locked on red_alert and police") {
    Rig rig;
    rig.lamp.next_anim();  // red_alert
    rig.run(1.0f, 0.0f, 2000);
    REQUIRE(rig.lamp.hue_for_current() == 0.0f);

    rig.lamp.next_anim();  // police
    rig.run(1.0f, 0.0f, 2000);
    REQUIRE(rig.lamp.hue_for_current() == 0.0f);
}

TEST_CASE("police strobe alternates blue and red phases at 50% duty") {
    Police anim;
    hsva_t buf[4];
    PixelView dst;
    REQUIRE(dst.initialize(buf, 4));

    anim.render(dst, 0.01f);  // lit half of the blue phase's first flash
    REQUIRE(buf[0].h == 240.0f);
    REQUIRE(buf[0].v == 1.0f);
    REQUIRE(buf[3].v == 1.0f);

    anim.render(dst, 0.06f);  // dark half of the same flash
    REQUIRE(buf[0].v == 0.0f);

    anim.render(dst, 0.51f);  // red phase
    REQUIRE(buf[0].h == 0.0f);
    REQUIRE(buf[0].v == 1.0f);

    anim.render(dst, 1.01f);  // wraps back to blue
    REQUIRE(buf[0].h == 240.0f);
}

TEST_CASE("median5 picks the middle sample in any order") {
    uint16_t a[5] = {2000, 2010, 4095, 1990, 2005};  // one spike
    REQUIRE(median5(a) == 2005);

    uint16_t b[5] = {5, 4, 3, 2, 1};
    REQUIRE(median5(b) == 3);

    uint16_t c[5] = {7, 7, 0, 7, 7};
    REQUIRE(median5(c) == 7);
}

TEST_CASE("normalize_stick maps around an off-center rest point") {
    // Centered: full scale both ways.
    REQUIRE(normalize_stick(2048, 2048, 4095) == 0.0f);
    REQUIRE(normalize_stick(4095, 2048, 4095) == 1.0f);
    REQUIRE(normalize_stick(0, 2048, 4095) == -1.0f);

    // Off-center: each side scales by its own span.
    REQUIRE(normalize_stick(500, 1000, 4095) == Approx(-0.5f));
    REQUIRE(normalize_stick(4095, 1000, 4095) == 1.0f);
    REQUIRE(normalize_stick(1000 + 3095 / 5, 1000, 4095) ==
            Approx(0.2f).margin(0.001f));

    // Degenerate centers: a zero-width span reads as centered, not a crash.
    REQUIRE(normalize_stick(0, 0, 4095) == 0.0f);
    REQUIRE(normalize_stick(4095, 4095, 4095) == 0.0f);
}

TEST_CASE("police strobe flashes five times per color phase") {
    Police anim;
    hsva_t buf[1];
    PixelView dst;
    REQUIRE(dst.initialize(buf, 1));

    // Sample between the 50 ms duty edges so float rounding at the
    // boundaries cannot fake a transition.
    int rises = 0;
    bool prev = false;
    for (int i = 0; i < 100; i++) {
        anim.render(dst, 0.0025f + i * 0.005f);
        const bool lit = buf[0].v > 0.5f;
        if (lit && !prev) rises++;
        prev = lit;
    }
    REQUIRE(rises == Police::FLASHES_PER_PHASE);
}
