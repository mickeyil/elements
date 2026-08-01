#pragma once

#include <cstdint>

#include "core/animation.h"

// Platform-free logic for the joystick lamp, host-tested; the sketch feeds
// LampState::update() once per frame and reads the accessors.
//
// LampState holds the user-facing state (intensity, current animation,
// per-animation hue) and treats the stick as a proportional rate control:
// horizontal deflection rotates the current animation's hue, vertical
// ramps intensity, both at a rate that scales with how far the stick is
// pushed. The stick button cycles animations via next_anim(); power is
// handled by the wall cord, not here.

// Wrap a hue into [0, 360).
float wrap360(float degrees);

class LampState {
public:
    static constexpr int NUM_ANIMS = 4;
    // Indices match g_anims in the sketch: pacifica, red_alert, police,
    // soft. The fixed-color animations ignore the hue control.
    static constexpr bool HUE_LOCKED[NUM_ANIMS] = {false, true, true, false};
    // Inside the deadzone the stick does nothing; beyond it the rate rises
    // linearly, reaching the max at full deflection.
    static constexpr float DEADZONE = 0.20f;
    static constexpr float MAX_INTENSITY_PER_MS = 1.0f / 2000.0f;  // full range in 2 s
    static constexpr float MAX_HUE_DEG_PER_MS = 360.0f / 4500.0f;  // full turn in 4.5 s

    // x and y in [-1, 1], centered at rest; dt_ms is the frame time. The
    // axes act independently, so a diagonal adjusts hue and intensity
    // together.
    void update(float x, float y, uint32_t dt_ms);

    // Advance to the next animation; hue offsets are kept per animation.
    void next_anim();

    float intensity() const { return _intensity; }
    float hue_for_current() const { return _hue[_anim]; }
    int anim_index() const { return _anim; }

private:
    float _intensity = 1.0f;
    float _hue[NUM_ANIMS] = {};
    int _anim = 0;
};

// Police strobe: the whole ring flashes one color at 50% duty, five
// flashes per half second, alternating blue and red each phase.
class Police : public Animation {
public:
    static constexpr float COLOR_PHASE_S = 0.5f;  // one color's flash burst
    static constexpr int FLASHES_PER_PHASE = 5;
    static constexpr float FLASH_PERIOD_S = COLOR_PHASE_S / FLASHES_PER_PHASE;

    void render(PixelView& dst, float t_animation) override;
};
