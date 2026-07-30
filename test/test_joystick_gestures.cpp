#include <catch2/catch_test_macros.hpp>
#include <catch2/catch_approx.hpp>

#include "app/joystick_gestures.h"

using Catch::Approx;

namespace {

constexpr uint32_t STEP_MS = 10;

// Drives gestures and lamp together with a monotonic clock, stepping in
// STEP_MS ticks like the ~50 fps firmware loop.
struct Rig {
    JoystickGestures gestures;
    LampState lamp;
    uint32_t now = 1000;

    GestureResult step(float x, float y) {
        now += STEP_MS;
        GestureResult r = gestures.update(x, y, now);
        lamp.apply(r);
        return r;
    }

    // Deflect one axis so the last deflected update lands hold_ms after
    // engagement, then return to center. For long holds the integrated
    // hold time is exactly hold_ms - LONG_MS.
    void press(GestureAxis axis, float sign, uint32_t hold_ms) {
        const float x = axis == GestureAxis::Horizontal ? sign : 0.0f;
        const float y = axis == GestureAxis::Vertical ? sign : 0.0f;
        step(x, y);
        const uint32_t engaged_at = now;
        while (now < engaged_at + hold_ms) step(x, y);
        step(0.0f, 0.0f);
    }
};

}  // namespace

TEST_CASE("short vertical press toggles power, either direction") {
    Rig rig;
    REQUIRE(rig.lamp.on());

    rig.press(GestureAxis::Vertical, 1.0f, 100);
    REQUIRE_FALSE(rig.lamp.on());

    rig.press(GestureAxis::Vertical, -1.0f, 100);
    REQUIRE(rig.lamp.on());
}

TEST_CASE("deflection at the long threshold is not a short press") {
    JoystickGestures g;

    // Released at 399 ms: still a short press.
    g.update(0.0f, 1.0f, 1000);
    GestureResult r = g.update(0.0f, 0.0f, 1399);
    REQUIRE(r.short_press);
    REQUIRE(r.axis == GestureAxis::Vertical);

    // Released at 400 ms exactly: long, so no short press.
    g.update(0.0f, 1.0f, 2000);
    r = g.update(0.0f, 0.0f, 2400);
    REQUIRE_FALSE(r.short_press);
    REQUIRE_FALSE(r.hold);
}

TEST_CASE("release after a long hold emits nothing") {
    Rig rig;
    bool saw_short = false;
    for (uint32_t t = 0; t < 1000; t += STEP_MS) {
        saw_short |= rig.step(0.0f, 1.0f).short_press;
    }
    GestureResult r = rig.step(0.0f, 0.0f);
    REQUIRE_FALSE(saw_short);
    REQUIRE_FALSE(r.short_press);
    REQUIRE_FALSE(r.hold);
    REQUIRE(rig.lamp.on());
}

TEST_CASE("deadzone engage and release hysteresis") {
    Rig rig;

    // Below the engage threshold: never engages, never fires.
    for (uint32_t t = 0; t < 200; t += STEP_MS) rig.step(0.0f, 0.18f);
    GestureResult r = rig.step(0.0f, 0.0f);
    REQUIRE_FALSE(r.short_press);
    REQUIRE(rig.lamp.on());

    // Engage, sag into the hysteresis band, then release below 0.15.
    rig.step(0.0f, 0.25f);
    rig.step(0.0f, 0.17f);  // still engaged
    r = rig.step(0.0f, 0.10f);
    REQUIRE(r.short_press);
    REQUIRE(r.axis == GestureAxis::Vertical);
    REQUIRE_FALSE(rig.lamp.on());
}

TEST_CASE("diagonal deflection is ignored") {
    Rig rig;
    for (uint32_t t = 0; t < 1000; t += STEP_MS) {
        GestureResult r = rig.step(0.5f, 0.5f);
        REQUIRE_FALSE(r.short_press);
        REQUIRE_FALSE(r.hold);
    }
    GestureResult r = rig.step(0.0f, 0.0f);
    REQUIRE_FALSE(r.short_press);
    REQUIRE(rig.lamp.on());
    REQUIRE(rig.lamp.anim_index() == 0);
}

TEST_CASE("intensity ramps at 1.0 per 4 s and clamps") {
    Rig rig;

    // 2 s of ramping down after the 400 ms threshold: -0.5.
    rig.press(GestureAxis::Vertical, -1.0f, JoystickGestures::LONG_MS + 2000);
    REQUIRE(rig.lamp.intensity() == Approx(0.5f).margin(0.01f));
    REQUIRE(rig.lamp.on());

    // 10 more seconds down: clamps at 0.
    rig.press(GestureAxis::Vertical, -1.0f, JoystickGestures::LONG_MS + 10000);
    REQUIRE(rig.lamp.intensity() == 0.0f);

    // 10 seconds up: clamps at 1.
    rig.press(GestureAxis::Vertical, 1.0f, JoystickGestures::LONG_MS + 10000);
    REQUIRE(rig.lamp.intensity() == 1.0f);
}

TEST_CASE("short horizontal press cycles animations with wraparound") {
    Rig rig;
    REQUIRE(rig.lamp.anim_index() == 0);

    rig.press(GestureAxis::Horizontal, 1.0f, 100);
    REQUIRE(rig.lamp.anim_index() == 1);
    rig.press(GestureAxis::Horizontal, 1.0f, 100);
    REQUIRE(rig.lamp.anim_index() == 2);
    rig.press(GestureAxis::Horizontal, 1.0f, 100);
    REQUIRE(rig.lamp.anim_index() == 0);

    rig.press(GestureAxis::Horizontal, -1.0f, 100);
    REQUIRE(rig.lamp.anim_index() == 2);
}

TEST_CASE("hue rotates at 40 deg per s, wraps, and is per animation") {
    Rig rig;

    // 1 s of rotation after the threshold: +40 degrees on animation 0.
    rig.press(GestureAxis::Horizontal, 1.0f, JoystickGestures::LONG_MS + 1000);
    REQUIRE(rig.lamp.hue_for_current() == Approx(40.0f).margin(0.1f));

    // Animation 1 starts untouched, gets its own -80 degrees (wrapped).
    rig.press(GestureAxis::Horizontal, 1.0f, 100);
    REQUIRE(rig.lamp.anim_index() == 1);
    REQUIRE(rig.lamp.hue_for_current() == 0.0f);
    rig.press(GestureAxis::Horizontal, -1.0f, JoystickGestures::LONG_MS + 2000);
    REQUIRE(rig.lamp.hue_for_current() == Approx(280.0f).margin(0.1f));

    // Switching back restores animation 0's offset.
    rig.press(GestureAxis::Horizontal, -1.0f, 100);
    REQUIRE(rig.lamp.anim_index() == 0);
    REQUIRE(rig.lamp.hue_for_current() == Approx(40.0f).margin(0.1f));
}

TEST_CASE("while off only short vertical is heard") {
    Rig rig;
    rig.press(GestureAxis::Horizontal, 1.0f, JoystickGestures::LONG_MS + 1000);
    const float hue_before = rig.lamp.hue_for_current();

    rig.press(GestureAxis::Vertical, 1.0f, 100);
    REQUIRE_FALSE(rig.lamp.on());

    rig.press(GestureAxis::Horizontal, 1.0f, 100);
    rig.press(GestureAxis::Horizontal, -1.0f, JoystickGestures::LONG_MS + 3000);
    rig.press(GestureAxis::Vertical, -1.0f, JoystickGestures::LONG_MS + 3000);
    REQUIRE_FALSE(rig.lamp.on());
    REQUIRE(rig.lamp.anim_index() == 0);
    REQUIRE(rig.lamp.hue_for_current() == Approx(hue_before));
    REQUIRE(rig.lamp.intensity() == 1.0f);

    rig.press(GestureAxis::Vertical, -1.0f, 100);
    REQUIRE(rig.lamp.on());
}

TEST_CASE("toggling off and on restores the remembered intensity") {
    Rig rig;
    rig.press(GestureAxis::Vertical, -1.0f, JoystickGestures::LONG_MS + 2000);
    const float dimmed = rig.lamp.intensity();
    REQUIRE(dimmed == Approx(0.5f).margin(0.01f));

    rig.press(GestureAxis::Vertical, 1.0f, 100);
    REQUIRE_FALSE(rig.lamp.on());
    rig.press(GestureAxis::Vertical, 1.0f, 100);
    REQUIRE(rig.lamp.on());
    REQUIRE(rig.lamp.intensity() == dimmed);
}
