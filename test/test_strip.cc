// g++ -g -Wall -I . -o test/test_strip test/test_strip.cc src/strip.cc
#include <cassert>
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <cstring>

#include "src/strip.h"

static uint8_t s1[2][3] = {
  {10, 20, 30},
  {100, 200, 1},
};

int main()
{
  uint8_t *stripdata = (uint8_t*)malloc(6);
  memcpy(stripdata, s1, 6);

  Strip strip_rgb(stripdata, 2);  // RGB
  Strip strip_bgr(stripdata, 2, STRIP_BGR);

  assert(strip_rgb.pixel(0, PIXEL_R) == 10);
  assert(strip_rgb.pixel(1, PIXEL_R) == 100);
  assert(strip_bgr.pixel(0, PIXEL_R) == 30);
  assert(strip_bgr.pixel(1, PIXEL_R) == 1);

  strip_rgb.pixel(0, PIXEL_R) = 13;
  assert(strip_rgb.pixel(0, PIXEL_R) == 13);

  free(stripdata);

  return 0;
}