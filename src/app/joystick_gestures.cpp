#include "app/joystick_gestures.h"

#include <cmath>

float wrap360(float degrees)
{
    degrees = std::fmod(degrees, 360.0f);
    return degrees < 0.0f ? degrees + 360.0f : degrees;
}

GestureResult JoystickGestures::update(float x, float y, uint32_t now_ms)
{
    GestureResult r;

    if (!_engaged) {
        const bool out_x = std::fabs(x) >= ENGAGE;
        const bool out_y = std::fabs(y) >= ENGAGE;
        // Engage on exactly one deflected axis; diagonals stay idle.
        if (out_x == out_y) return r;
        _engaged = true;
        _long = false;
        _axis = out_x ? GestureAxis::Horizontal : GestureAxis::Vertical;
        const float v = out_x ? x : y;
        _sign = v > 0.0f ? 1 : -1;
        _start_ms = now_ms;
        return r;
    }

    const float v = _axis == GestureAxis::Horizontal ? x : y;
    if (std::fabs(v) < RELEASE) {
        if (now_ms - _start_ms < LONG_MS) {
            r.short_press = true;
            r.axis = _axis;
            r.sign = _sign;
        }
        _engaged = false;
        return r;
    }

    if (now_ms - _start_ms >= LONG_MS) {
        if (!_long) {
            _long = true;
            // Ramping starts at the threshold, not at engagement.
            _last_hold_ms = _start_ms + LONG_MS;
        }
        r.hold = true;
        r.axis = _axis;
        r.sign = _sign;
        r.hold_dt_ms = now_ms - _last_hold_ms;
        _last_hold_ms = now_ms;
    }
    return r;
}

void LampState::apply(const GestureResult& g)
{
    if (g.short_press && g.axis == GestureAxis::Vertical) {
        _on = !_on;
        return;
    }
    if (!_on) return;

    if (g.short_press && g.axis == GestureAxis::Horizontal) {
        _anim = (_anim + (g.sign > 0 ? 1 : NUM_ANIMS - 1)) % NUM_ANIMS;
        return;
    }

    if (g.hold && g.axis == GestureAxis::Vertical) {
        // Cast before multiplying: int8 * uint32 would go unsigned.
        _intensity += float(g.sign) * float(g.hold_dt_ms) * INTENSITY_PER_MS;
        if (_intensity < 0.0f) _intensity = 0.0f;
        if (_intensity > 1.0f) _intensity = 1.0f;
        return;
    }

    if (g.hold && g.axis == GestureAxis::Horizontal) {
        _hue[_anim] = wrap360(
            _hue[_anim] + float(g.sign) * float(g.hold_dt_ms) * HUE_DEG_PER_MS);
    }
}
