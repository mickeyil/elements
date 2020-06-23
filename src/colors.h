#pragma once

#include <cstdint>

struct hsv_t
{
  float h;
  float s;
  float v;

  hsv_t() { }
  hsv_t(float _h, float _s, float _v) : h(_h), s(_s), v(_v) { }
};


typedef struct {
  uint8_t r;
  uint8_t g;
  uint8_t b;
} rgb_t;

void hsv2rgb(int hue, int sat, int val, uint8_t& r_out, uint8_t& g_out, uint8_t& b_out);
