#pragma once

#include <cstdint>

struct hsv_t
{
  float h;
  float s;
  float v;

  hsv_t() { }
  hsv_t(float _h, float _s, float _v) : h(_h), s(_s), v(_v) { }
  
  // accessor by channel number. h=0, s=1, v=2
  float value_by_ch(unsigned int ch) const {
    switch (ch) {
      case 0:
        return h;
      case 1:
        return s;
      case 2:
        return v;
    }
    return 0.0;
  }
};

struct hsvu8_t
{
  uint8_t h;
  uint8_t s;
  uint8_t v;

  hsvu8_t() { }
  hsvu8_t(uint8_t _h, uint8_t _s, uint8_t _v) : h(_h), s(_s), v(_v) { }
  hsv_t to_hsv_t() const {
    hsv_t hsv((float) h, (float) s, (float) v);
    return hsv;
  }
};

typedef struct {
  uint8_t r;
  uint8_t g;
  uint8_t b;
} rgb_t;

void hsv2rgb(int hue, int sat, int val, uint8_t& r_out, uint8_t& g_out, uint8_t& b_out);
