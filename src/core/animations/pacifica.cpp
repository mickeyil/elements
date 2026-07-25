#include "core/animations/pacifica.h"

#include "core/pixel_view.h"

#include <cmath>
#include <new>

namespace {

bool read_finite_f32(BlobReader& r, float& out) {
    return r.read_f32_le(out) && std::isfinite(out);
}

rgb_t hex(uint32_t v) {
    return rgb_t(static_cast<uint8_t>(v >> 16), static_cast<uint8_t>(v >> 8),
                 static_cast<uint8_t>(v));
}

// Blue-green palettes from the waters off southern California
// (FastLED examples/Pacifica).
const fx::palette16 palette_1 = {{
    hex(0x000507), hex(0x000409), hex(0x00030B), hex(0x00030D),
    hex(0x000210), hex(0x000212), hex(0x000114), hex(0x000117),
    hex(0x000019), hex(0x00001C), hex(0x000026), hex(0x000031),
    hex(0x00003B), hex(0x000046), hex(0x14554B), hex(0x28AA50),
}};
const fx::palette16 palette_2 = {{
    hex(0x000507), hex(0x000409), hex(0x00030B), hex(0x00030D),
    hex(0x000210), hex(0x000212), hex(0x000114), hex(0x000117),
    hex(0x000019), hex(0x00001C), hex(0x000026), hex(0x000031),
    hex(0x00003B), hex(0x000046), hex(0x0C5F52), hex(0x19BE5F),
}};
const fx::palette16 palette_3 = {{
    hex(0x000208), hex(0x00030E), hex(0x000514), hex(0x00061A),
    hex(0x000820), hex(0x000927), hex(0x000B2D), hex(0x000C33),
    hex(0x000E39), hex(0x001040), hex(0x001450), hex(0x001860),
    hex(0x001C70), hex(0x002080), hex(0x1040BF), hex(0x2060FF),
}};

double omega(double bpm) {
    // rad per ms for a sine at `bpm` cycles per minute.
    return 2.0 * M_PI * bpm / 60000.0;
}

// Integral of (A + B sin(wa t)) * (C + D sin(wb t)) over 0..t_ms.
//
// This replaces the original's per-frame `sCIStart += deltams * beatsin(...)`
// accumulators: each one integrates a speed that is a product of two slow
// sine waves, and that integral has this closed form. Requires wa != wb.
double drift_integral(double A, double B, double wa,
                      double C, double D, double wb, double t_ms)
{
    const double wm = wa - wb;
    const double wp = wa + wb;
    return A * C * t_ms
         - (A * D / wb) * (std::cos(wb * t_ms) - 1.0)
         - (B * C / wa) * (std::cos(wa * t_ms) - 1.0)
         + (B * D / 2.0) * (std::sin(wm * t_ms) / wm - std::sin(wp * t_ms) / wp);
}

uint16_t wrap_u16(double v) {
    double w = std::fmod(v, 65536.0);
    if (w < 0.0) w += 65536.0;
    return static_cast<uint16_t>(w);
}

}  // namespace

Pacifica::Pacifica(const PacificaParams& p) : _p(p) {}

Pacifica::~Pacifica()
{
    delete[] _scratch;
}

bool Pacifica::allocate_scratch(uint16_t size)
{
    if (size == 0) return false;
    delete[] _scratch;
    _scratch = new (std::nothrow) rgb_t[size];
    _scratch_size = (_scratch != nullptr) ? size : 0;
    return _scratch != nullptr;
}

void Pacifica::render_wave_layer(const fx::palette16& pal, uint16_t n,
                                 uint16_t cistart, uint16_t wavescale,
                                 uint8_t bri, uint16_t ioff)
{
    uint16_t ci = cistart;
    uint16_t waveangle = ioff;
    const uint16_t wavescale_half = (wavescale / 2) + 20;
    for (uint16_t i = 0; i < n; i++) {
        waveangle += 250;
        const uint16_t s16 = fx::sin16(waveangle) + 32768;
        const uint16_t cs = fx::scale16(s16, wavescale_half) + wavescale_half;
        ci += cs;
        const uint16_t sindex16 = fx::sin16(ci) + 32768;
        const uint8_t sindex8 = fx::scale16(sindex16, 240);
        const rgb_t c = fx::color_from_palette(pal, sindex8, bri);
        _scratch[i].r = fx::qadd8(_scratch[i].r, c.r);
        _scratch[i].g = fx::qadd8(_scratch[i].g, c.g);
        _scratch[i].b = fx::qadd8(_scratch[i].b, c.b);
    }
}

void Pacifica::add_whitecaps(uint16_t n, uint32_t ms)
{
    const uint8_t basethreshold = fx::beatsin8(9, 55, 65, ms);
    uint8_t wave = fx::beat8(7, ms);
    for (uint16_t i = 0; i < n; i++) {
        const uint8_t threshold = fx::scale8(fx::sin8(wave), 20) + basethreshold;
        wave += 7;
        const uint8_t l = fx::average_light(_scratch[i]);
        if (l > threshold) {
            const uint8_t overage = l - threshold;
            const uint8_t overage2 = fx::qadd8(overage, overage);
            _scratch[i].r = fx::qadd8(_scratch[i].r, overage);
            _scratch[i].g = fx::qadd8(_scratch[i].g, overage2);
            _scratch[i].b = fx::qadd8(_scratch[i].b, fx::qadd8(overage2, overage2));
        }
    }
}

void Pacifica::deepen_colors(uint16_t n)
{
    for (uint16_t i = 0; i < n; i++) {
        _scratch[i].b = fx::scale8(_scratch[i].b, 145);
        _scratch[i].g = fx::scale8(_scratch[i].g, 200);
        if (_scratch[i].r < 2) _scratch[i].r = 2;
        if (_scratch[i].g < 5) _scratch[i].g = 5;
        if (_scratch[i].b < 7) _scratch[i].b = 7;
    }
}

void Pacifica::render(PixelView& dst, float t_animation)
{
    if (_scratch == nullptr || dst.size() > _scratch_size) {
        return;
    }
    const uint16_t n = dst.size();

    const double t_ms = static_cast<double>(t_animation) * 1000.0 * _p.speed;
    const uint32_t ms =
        static_cast<uint32_t>(std::fmod(t_ms, 4294967296.0));

    // Wave-layer speeds from the original: two slow speed factors
    // (beatsin16(3|4, 179, 269), divided by 256) modulating four per-layer
    // scroll rates (beatsin88 of the constants below).
    const double sfA = 224.0 / 256.0, sfB = 45.0 / 256.0;
    const double w_sf1 = omega(3.0), w_sf2 = omega(4.0);
    const uint16_t ci1 = wrap_u16(
        drift_integral(sfA, sfB, w_sf1, 11.5, 1.5, omega(1011.0 / 256.0), t_ms));
    const uint16_t ci2 = wrap_u16(-0.5 * (
        drift_integral(sfA, sfB, w_sf1, 9.5, 1.5, omega(777.0 / 256.0), t_ms) +
        drift_integral(sfA, sfB, w_sf2, 9.5, 1.5, omega(777.0 / 256.0), t_ms)));
    const uint16_t ci3 = wrap_u16(
        -drift_integral(sfA, sfB, w_sf1, 6.0, 1.0, omega(501.0 / 256.0), t_ms));
    const uint16_t ci4 = wrap_u16(
        -drift_integral(sfA, sfB, w_sf2, 5.0, 1.0, omega(257.0 / 256.0), t_ms));

    for (uint16_t i = 0; i < n; i++) {
        _scratch[i] = rgb_t(2, 6, 10);
    }

    render_wave_layer(palette_1, n, ci1,
                      fx::beatsin16(3, 11 * 256, 14 * 256, ms),
                      fx::beatsin8(10, 70, 130, ms),
                      static_cast<uint16_t>(0) - fx::beat16(301, ms));
    render_wave_layer(palette_2, n, ci2,
                      fx::beatsin16(4, 6 * 256, 9 * 256, ms),
                      fx::beatsin8(17, 40, 80, ms), fx::beat16(401, ms));
    render_wave_layer(palette_3, n, ci3, 6 * 256,
                      fx::beatsin8(9, 10, 38, ms),
                      static_cast<uint16_t>(0) - fx::beat16(503, ms));
    render_wave_layer(palette_3, n, ci4, 5 * 256,
                      fx::beatsin8(8, 10, 28, ms), fx::beat16(601, ms));

    add_whitecaps(n, ms);
    deepen_colors(n);

    for (uint16_t i = 0; i < n; i++) {
        hsva_t c = rgb_to_hsv(_scratch[i]);
        c.h = std::fmod(c.h + _p.hue_shift, 360.0f);
        if (c.h < 0.0f) c.h += 360.0f;
        c.v *= _p.brightness;
        dst[i] = c;
    }
}

Animation* Pacifica::from_blob(const uint8_t* params, size_t params_size,
                               DecodeError* err_out)
{
    *err_out = DecodeError::InvalidField;
    BlobReader r(params, params_size);
    PacificaParams p;

    if (!read_finite_f32(r, p.speed))          return nullptr;
    if (p.speed <= 0.0f)                       return nullptr;
    if (!read_finite_f32(r, p.brightness))     return nullptr;
    if (p.brightness < 0.0f || p.brightness > 1.0f) return nullptr;
    if (!read_finite_f32(r, p.hue_shift))      return nullptr;

    Pacifica* anim = new (std::nothrow) Pacifica(p);
    if (anim == nullptr) {
        *err_out = DecodeError::OutOfMemory;
        return nullptr;
    }
    *err_out = DecodeError::Ok;
    return anim;
}
