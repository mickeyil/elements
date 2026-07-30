#pragma once

#include <cstdint>

// Gesture layer for the joystick lamp. JoystickGestures turns raw stick
// deflections into short-press and hold gestures; LampState consumes those
// gestures and holds the user-facing lamp state (power, intensity, current
// animation, per-animation hue). Both are platform-free so the logic runs
// under host tests; the sketch feeds update() and reads the accessors.

// Wrap a hue into [0, 360).
float wrap360(float degrees);

enum class GestureAxis : uint8_t { Horizontal = 0, Vertical = 1 };

// At most one of short_press / hold is set per update.
struct GestureResult {
    bool short_press = false;   // deflection released before the long threshold
    bool hold = false;          // set on every update once the hold is long
    GestureAxis axis = GestureAxis::Horizontal;
    int8_t sign = 0;            // +1 = up/right, -1 = down/left
    uint32_t hold_dt_ms = 0;    // hold time this update covers; integrate it
};

// Detects gestures from normalized stick positions fed once per frame.
//
// A gesture starts when exactly one axis leaves the engage deadzone; if
// both are out (diagonal) nothing engages. The engaged axis releases at a
// lower threshold so jitter at the boundary cannot retrigger. Deflections
// shorter than LONG_MS become a short press on release; at LONG_MS the
// gesture turns into a hold and release emits nothing.
class JoystickGestures {
public:
    static constexpr float ENGAGE = 0.20f;
    static constexpr float RELEASE = 0.15f;
    static constexpr uint32_t LONG_MS = 400;

    // x and y in [-1, 1], centered at rest. now_ms must not go backwards.
    GestureResult update(float x, float y, uint32_t now_ms);

private:
    bool _engaged = false;
    bool _long = false;
    GestureAxis _axis = GestureAxis::Horizontal;
    int8_t _sign = 0;
    uint32_t _start_ms = 0;
    uint32_t _last_hold_ms = 0;
};

// Lamp state machine. Vertical short press toggles power (off keeps the
// intensity, on restores it). Vertical hold ramps intensity, horizontal
// short press cycles the animation, horizontal hold rotates the current
// animation's hue offset. While off, only the power toggle is heard.
class LampState {
public:
    static constexpr int NUM_ANIMS = 3;
    static constexpr float INTENSITY_PER_MS = 1.0f / 4000.0f;  // full range in 4 s
    static constexpr float HUE_DEG_PER_MS = 360.0f / 9000.0f;  // full turn in 9 s

    void apply(const GestureResult& g);

    bool on() const { return _on; }
    float intensity() const { return _intensity; }
    float hue_for_current() const { return _hue[_anim]; }
    int anim_index() const { return _anim; }

private:
    bool _on = true;
    float _intensity = 1.0f;
    float _hue[NUM_ANIMS] = {0.0f, 0.0f, 0.0f};
    int _anim = 0;
};
