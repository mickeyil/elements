#pragma once

#include "core/animation.h"

// Police strobe for the joystick lamp: the whole ring flashes one color at
// 50% duty, five flashes per half second, alternating blue and red each
// phase. Not part of the blob protocol; the lamp instantiates it directly.

class Police : public Animation {
public:
    static constexpr float COLOR_PHASE_S = 0.5f;  // one color's flash burst
    static constexpr int FLASHES_PER_PHASE = 5;
    static constexpr float FLASH_PERIOD_S = COLOR_PHASE_S / FLASHES_PER_PHASE;

    void render(PixelView& dst, float t_animation) override;
};
