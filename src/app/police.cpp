#include "app/police.h"

#include "core/colors.h"
#include "core/pixel_view.h"

#include <cmath>
#include <cstdint>

void Police::render(PixelView& dst, float t_animation)
{
    const float phase = std::fmod(t_animation, 2.0f * COLOR_PHASE_S);
    const float hue = phase < COLOR_PHASE_S ? 240.0f : 0.0f;  // blue, then red
    const bool lit =
        std::fmod(t_animation, FLASH_PERIOD_S) < FLASH_PERIOD_S * 0.5f;
    const hsva_t px(hue, 1.0f, lit ? 1.0f : 0.0f, 1.0f);
    for (uint16_t i = 0; i < dst.size(); i++) dst[i] = px;
}
