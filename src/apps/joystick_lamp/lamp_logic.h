#pragma once

#include <cstdint>

#include "core/animation.h"

// Platform-free logic for the joystick lamp, host-tested; the sketch feeds
// LampState::update() once per frame and reads the accessors.
//
// LampState holds the user-facing state (intensity, current animation,
// per-animation hue). Horizontal taps cycle the animation (right = next,
// left = previous); a horizontal hold rotates the current animation's hue
// at a fixed rate, where the animation allows it. Vertical deflection
// ramps intensity at a rate proportional to how far the stick is pushed.
// Power is handled by the wall cord, not here.

// Wrap a hue into [0, 360).
float wrap360(float degrees);

// Median of five samples; reorders s.
uint16_t median5(uint16_t s[5]);

// Map a raw ADC reading to [-1, 1] around the resting center. The two
// sides scale independently because the pot rarely rests at exactly half
// scale.
float normalize_stick(uint16_t raw, uint16_t center, uint16_t adc_max);

class LampState {
public:
    static constexpr int NUM_ANIMS = 4;
    // Indices match g_anims in the sketch: pacifica, red_alert, police,
    // soft. The fixed-color animations ignore the hue control.
    static constexpr bool HUE_LOCKED[NUM_ANIMS] = {false, true, true, false};
    // Inside the deadzone the stick does nothing. Vertically the intensity
    // rate rises linearly beyond it, reaching the max at full deflection.
    // Horizontally it is the gesture engage threshold; the engaged axis
    // releases at the lower threshold so jitter cannot retrigger.
    static constexpr float DEADZONE = 0.20f;
    static constexpr float RELEASE = 0.15f;
    // A horizontal deflection released before LONG_MS is a tap; at LONG_MS
    // it becomes a hold and release does nothing.
    static constexpr uint32_t LONG_MS = 400;
    static constexpr float MAX_INTENSITY_PER_MS = 1.0f / 2000.0f;  // full range in 2 s
    static constexpr float HUE_DEG_PER_MS = 360.0f / 4500.0f;  // full turn in 4.5 s

    // x and y in [-1, 1], centered at rest; dt_ms is the frame time. The
    // axes act independently: a vertical drift during a horizontal gesture
    // still ramps intensity.
    void update(float x, float y, uint32_t dt_ms);

    // Cycle the animation; hue offsets are kept per animation.
    void next_anim();
    void prev_anim();

    float intensity() const { return _intensity; }
    float hue_for_current() const { return _hue[_anim]; }
    int anim_index() const { return _anim; }

private:
    float _intensity = 0.5f;
    float _hue[NUM_ANIMS] = {};
    int _anim = 0;
    bool _h_engaged = false;
    int8_t _h_sign = 0;
    uint32_t _h_held_ms = 0;
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
