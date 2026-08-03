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

    // Deflect horizontally for deflect_ms, then return to center. The
    // engage frame does not count toward the held time, so the gesture is
    // held for deflect_ms - STEP_MS.
    void press_h(float sign, uint32_t deflect_ms) {
        run(sign, 0.0f, deflect_ms);
        run(0.0f, 0.0f, STEP_MS);
    }
};

}  // namespace

TEST_CASE("deflection inside the deadzone changes nothing") {
    Rig rig;
    rig.run(0.19f, -0.19f, 2000);
    rig.run(0.0f, 0.0f, STEP_MS);
    REQUIRE(rig.lamp.intensity() == 0.5f);
    REQUIRE(rig.lamp.hue_for_current() == 0.0f);
    REQUIRE(rig.lamp.anim_index() == 0);
}

TEST_CASE("horizontal taps cycle animations both ways with wraparound") {
    Rig rig;
    REQUIRE(rig.lamp.anim_index() == 0);

    rig.press_h(1.0f, 100);
    REQUIRE(rig.lamp.anim_index() == 1);
    rig.press_h(1.0f, 100);
    rig.press_h(1.0f, 100);
    rig.press_h(1.0f, 100);
    REQUIRE(rig.lamp.anim_index() == 0);

    rig.press_h(-1.0f, 100);
    REQUIRE(rig.lamp.anim_index() == 3);
}

TEST_CASE("deflection at the long threshold is not a tap") {
    Rig rig;

    // Held 400 ms exactly: a hold, so no animation change (and no
    // rotation either -- the hold time past the threshold is zero).
    rig.press_h(1.0f, 410);
    REQUIRE(rig.lamp.anim_index() == 0);
    REQUIRE(rig.lamp.hue_for_current() == 0.0f);

    // Held 390 ms: still a tap.
    rig.press_h(1.0f, 400);
    REQUIRE(rig.lamp.anim_index() == 1);
}

TEST_CASE("hold rotates hue at 80 deg per s past the threshold") {
    Rig rig;

    // Held 1400 ms: 1000 ms of rotation after the 400 ms threshold.
    rig.press_h(1.0f, 1410);
    REQUIRE(rig.lamp.anim_index() == 0);
    REQUIRE(rig.lamp.hue_for_current() == Approx(80.0f).margin(0.5f));

    // Held 900 ms leftward: -40 degrees, wrapped.
    rig.press_h(-1.0f, 910);
    REQUIRE(rig.lamp.hue_for_current() == Approx(40.0f).margin(0.5f));
}

TEST_CASE("engage and release hysteresis") {
    Rig rig;

    // Engage at 0.25, sag into the band, release below 0.15: the sagging
    // frame keeps the gesture alive and the release lands as a tap.
    rig.lamp.update(0.25f, 0.0f, STEP_MS);
    rig.lamp.update(0.17f, 0.0f, STEP_MS);
    rig.lamp.update(0.10f, 0.0f, STEP_MS);
    REQUIRE(rig.lamp.anim_index() == 1);
}

TEST_CASE("intensity starts at 50% and sweeps the full range in 2 s") {
    Rig rig;
    REQUIRE(rig.lamp.intensity() == 0.5f);

    rig.run(0.0f, -1.0f, 500);
    REQUIRE(rig.lamp.intensity() == Approx(0.25f).margin(0.01f));

    rig.run(0.0f, -1.0f, 5000);
    REQUIRE(rig.lamp.intensity() == 0.0f);

    rig.run(0.0f, 1.0f, 5000);
    REQUIRE(rig.lamp.intensity() == 1.0f);
}

TEST_CASE("intensity rate scales with displacement") {
    // 0.6 deflection is halfway through the active range: half the max
    // rate, so 1 s drops 0.25.
    Rig rig;
    rig.run(0.0f, -0.6f, 1000);
    REQUIRE(rig.lamp.intensity() == Approx(0.25f).margin(0.01f));
}

TEST_CASE("vertical still ramps during a horizontal hold") {
    Rig rig;
    rig.run(1.0f, -1.0f, 1000);
    REQUIRE(rig.lamp.intensity() == Approx(0.0f).margin(0.001f));
    // Engaged on the first frame, so 990 ms held: 590 ms of rotation.
    REQUIRE(rig.lamp.hue_for_current() == Approx(47.2f).margin(0.5f));
}

TEST_CASE("hue offsets are kept per animation") {
    Rig rig;
    rig.press_h(1.0f, 1410);  // +80 degrees on pacifica
    const float hue0 = rig.lamp.hue_for_current();
    REQUIRE(hue0 == Approx(80.0f).margin(0.5f));

    rig.press_h(-1.0f, 100);  // tap left: wraps to soft
    REQUIRE(rig.lamp.anim_index() == 3);
    REQUIRE(rig.lamp.hue_for_current() == 0.0f);
    rig.press_h(-1.0f, 910);  // -40 degrees on soft
    REQUIRE(rig.lamp.hue_for_current() == Approx(320.0f).margin(0.5f));

    rig.press_h(1.0f, 100);  // tap right: back to pacifica
    REQUIRE(rig.lamp.anim_index() == 0);
    REQUIRE(rig.lamp.hue_for_current() == Approx(hue0));
}

TEST_CASE("hue is locked on red_alert and police, taps still work") {
    Rig rig;
    rig.press_h(1.0f, 100);  // red_alert
    rig.press_h(1.0f, 2000);
    REQUIRE(rig.lamp.anim_index() == 1);
    REQUIRE(rig.lamp.hue_for_current() == 0.0f);

    rig.press_h(1.0f, 100);  // police
    rig.press_h(1.0f, 2000);
    REQUIRE(rig.lamp.anim_index() == 2);
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
