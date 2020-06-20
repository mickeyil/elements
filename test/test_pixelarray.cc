// g++ -g -DDEBUG_HELPERS -Wall -Wno-unused-function -I . -o test/test_pixelarray test/test_pixelarray.cc src/pixelarray.cc src/colors.cc src/strip.cc
#include <cassert>
#include <cstdlib>
#include "src/pixelarray.h"

static float fa1[2][3] = {
  {  0.0, 255.0, 255.0},
  {120.0, 255.0, 255.0},
};

static uint8_t ia1[3] = { 0, 1 };

static rgb_t stripdata[2];

int main()
{
  PixelArray pa1(2, (hsv_t*) fa1, ia1);
  pa1.print();
  assert(pa1[0].h == 0.0);
  assert(pa1[1].h == 120.0);
  
  for (int i =0; i < 2; i++) {
    stripdata[i].r = 255;
    stripdata[i].g = 255;
    stripdata[i].b = 255;
  }
  
  Strip strip_rgb((uint8_t*) stripdata, 2);  // RGB
  strip_rgb.print();

  pa1.hsv_to_rgb_strip(strip_rgb);
  strip_rgb.print();

  return 0;
}