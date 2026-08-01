#include "app/joystick_gestures.h"

#include <cmath>

float wrap360(float degrees)
{
    degrees = std::fmod(degrees, 360.0f);
    return degrees < 0.0f ? degrees + 360.0f : degrees;
}

namespace {

// Signed response in [-1, 1]: zero inside the deadzone, then linear up to
// full deflection.
float response(float v)
{
    const float mag = std::fabs(v);
    if (mag < LampState::DEADZONE) return 0.0f;
    float r = (mag - LampState::DEADZONE) / (1.0f - LampState::DEADZONE);
    if (r > 1.0f) r = 1.0f;
    return v < 0.0f ? -r : r;
}

}  // namespace

void LampState::update(float x, float y, uint32_t dt_ms)
{
    const float dt = float(dt_ms);
    if (!HUE_LOCKED[_anim]) {
        _hue[_anim] =
            wrap360(_hue[_anim] + response(x) * MAX_HUE_DEG_PER_MS * dt);
    }
    _intensity += response(y) * MAX_INTENSITY_PER_MS * dt;
    if (_intensity < 0.0f) _intensity = 0.0f;
    if (_intensity > 1.0f) _intensity = 1.0f;
}

void LampState::next_anim()
{
    _anim = (_anim + 1) % NUM_ANIMS;
}
