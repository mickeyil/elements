#pragma once

#include <cstdint>

#include "core/colors.h"

// Integer wave/scale helpers ported from FastLED's lib8tion (MIT), with
// FASTLED_SCALE8_FIXED semantics. Animations ported from FastLED effects
// (Pacifica and friends) call these to reproduce the original output; keep
// the math bit-identical to the library, not "close enough".
//
// The beat helpers take milliseconds explicitly instead of reading a clock,
// so callers stay pure functions of animation time.

namespace fx {

// i * (scale+1) / 256; scale8(x, 255) == x.
uint8_t scale8(uint8_t i, uint8_t scale);

// i * (scale+1) / 65536; scale16(x, 65535) == x.
uint16_t scale16(uint16_t i, uint16_t scale);

// Saturating add: min(i + j, 255).
uint8_t qadd8(uint8_t i, uint8_t j);

// Piecewise-linear sine. Full cycle over theta 0..65535; output -32767..32767.
int16_t sin16(uint16_t theta);

// Piecewise-linear sine. Full cycle over theta 0..255; output 0..255
// centered on 128.
uint8_t sin8(uint8_t theta);

// Sawtooth phase at `bpm88` beats per minute (Q8.8); one beat = 65536 ticks.
uint16_t beat88(uint16_t bpm88, uint32_t ms);

// Same, with bpm < 256 taken as a plain integer BPM.
uint16_t beat16(uint16_t bpm, uint32_t ms);

// Top byte of beat16: one beat = 256 ticks.
uint8_t beat8(uint16_t bpm, uint32_t ms);

// Sine oscillating between lowest and highest at the given BPM.
uint16_t beatsin88(uint16_t bpm88, uint16_t lowest, uint16_t highest,
                   uint32_t ms);
uint16_t beatsin16(uint16_t bpm, uint16_t lowest, uint16_t highest,
                   uint32_t ms);
uint8_t beatsin8(uint16_t bpm, uint8_t lowest, uint8_t highest, uint32_t ms);

// (r + g + b) / 3, FastLED CRGB::getAverageLight.
uint8_t average_light(const rgb_t& c);

// 16-entry RGB palette, indexed by a 0..255 position.
struct palette16 {
    rgb_t entries[16];
};

// FastLED ColorFromPalette with LINEARBLEND: interpolates between adjacent
// entries (entry 15 wraps to entry 0), then scales by brightness.
rgb_t color_from_palette(const palette16& pal, uint8_t index,
                         uint8_t brightness);

}  // namespace fx
