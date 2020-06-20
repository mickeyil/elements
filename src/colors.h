#pragma once

#include <cstdint>

typedef struct {
  float h;
  float s;
  float v;
} hsv_t;


typedef struct {
  uint8_t r;
  uint8_t g;
  uint8_t b;
} rgb_t;

void hsv2rgb(int hue, int sat, int val, uint8_t& r_out, uint8_t& g_out, uint8_t& b_out);
