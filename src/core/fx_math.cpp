#include "core/fx_math.h"

namespace fx {

uint8_t scale8(uint8_t i, uint8_t scale)
{
    return (static_cast<uint16_t>(i) * (1 + static_cast<uint16_t>(scale))) >> 8;
}

uint16_t scale16(uint16_t i, uint16_t scale)
{
    return (static_cast<uint32_t>(i) * (1 + static_cast<uint32_t>(scale))) / 65536;
}

uint8_t qadd8(uint8_t i, uint8_t j)
{
    const unsigned int t = i + j;
    return t > 255 ? 255 : static_cast<uint8_t>(t);
}

int16_t sin16(uint16_t theta)
{
    static const uint16_t base[] = {0,     6393,  12539, 18204,
                                    23170, 27245, 30273, 32137};
    static const uint8_t slope[] = {49, 48, 44, 38, 31, 23, 14, 4};

    uint16_t offset = (theta & 0x3FFF) >> 3;  // 0..2047
    if (theta & 0x4000) offset = 2047 - offset;

    const uint8_t section = offset / 256;  // 0..7
    const uint16_t b = base[section];
    const uint8_t m = slope[section];

    const uint8_t secoffset8 = static_cast<uint8_t>(offset) / 2;

    const uint16_t mx = m * secoffset8;
    int16_t y = mx + b;

    if (theta & 0x8000) y = -y;
    return y;
}

uint8_t sin8(uint8_t theta)
{
    static const uint8_t b_m16_interleave[] = {0, 49, 49, 41, 90, 27, 117, 10};

    uint8_t offset = theta;
    if (theta & 0x40) {
        offset = static_cast<uint8_t>(255) - offset;
    }
    offset &= 0x3F;  // 0..63

    uint8_t secoffset = offset & 0x0F;  // 0..15
    if (theta & 0x40) ++secoffset;

    const uint8_t section = offset >> 4;  // 0..3
    const uint8_t* p = b_m16_interleave + section * 2;
    const uint8_t b = p[0];
    const uint8_t m16 = p[1];

    const uint8_t mx = (m16 * secoffset) >> 4;

    int8_t y = mx + b;
    if (theta & 0x80) y = -y;

    return static_cast<uint8_t>(y + 128);
}

uint16_t beat88(uint16_t bpm88, uint32_t ms)
{
    // 65536:60000 approximated as 280:256; accurate to about 0.05%.
    return (ms * bpm88 * 280) >> 16;
}

uint16_t beat16(uint16_t bpm, uint32_t ms)
{
    if (bpm < 256) bpm <<= 8;
    return beat88(bpm, ms);
}

uint8_t beat8(uint16_t bpm, uint32_t ms)
{
    return beat16(bpm, ms) >> 8;
}

uint16_t beatsin88(uint16_t bpm88, uint16_t lowest, uint16_t highest,
                   uint32_t ms)
{
    const uint16_t beat = beat88(bpm88, ms);
    const uint16_t beatsin = sin16(beat) + 32768;
    const uint16_t rangewidth = highest - lowest;
    return lowest + scale16(beatsin, rangewidth);
}

uint16_t beatsin16(uint16_t bpm, uint16_t lowest, uint16_t highest,
                   uint32_t ms)
{
    const uint16_t beat = beat16(bpm, ms);
    const uint16_t beatsin = sin16(beat) + 32768;
    const uint16_t rangewidth = highest - lowest;
    return lowest + scale16(beatsin, rangewidth);
}

uint8_t beatsin8(uint16_t bpm, uint8_t lowest, uint8_t highest, uint32_t ms)
{
    const uint8_t beat = beat8(bpm, ms);
    const uint8_t beatsin = sin8(beat);
    const uint8_t rangewidth = highest - lowest;
    return lowest + scale8(beatsin, rangewidth);
}

uint8_t average_light(const rgb_t& c)
{
    return scale8(c.r, 85) + scale8(c.g, 85) + scale8(c.b, 85);
}

rgb_t color_from_palette(const palette16& pal, uint8_t index,
                         uint8_t brightness)
{
    const uint8_t hi4 = index >> 4;
    const uint8_t lo4 = index & 0x0F;

    const rgb_t* entry = &pal.entries[hi4];
    uint8_t red1 = entry->r;
    uint8_t green1 = entry->g;
    uint8_t blue1 = entry->b;

    if (lo4) {
        entry = (hi4 == 15) ? &pal.entries[0] : entry + 1;

        const uint8_t f2 = lo4 << 4;
        const uint8_t f1 = 255 - f2;

        red1 = scale8(red1, f1) + scale8(entry->r, f2);
        green1 = scale8(green1, f1) + scale8(entry->g, f2);
        blue1 = scale8(blue1, f1) + scale8(entry->b, f2);
    }

    if (brightness != 255) {
        if (brightness) {
            ++brightness;  // adjust for rounding, as FastLED does
            if (red1) red1 = scale8(red1, brightness);
            if (green1) green1 = scale8(green1, brightness);
            if (blue1) blue1 = scale8(blue1, brightness);
        } else {
            red1 = green1 = blue1 = 0;
        }
    }

    return rgb_t(red1, green1, blue1);
}

}  // namespace fx
