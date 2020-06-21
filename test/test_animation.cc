// g++ -g -DDEBUG_HELPERS -Wall -Wno-unused-function -I . -I src/ -o test/test_animation test/test_animation.cc src/slotsmm.cc src/pixelarray.cc src/strip.cc src/colors.cc src/animation.cc src/animations/anim_fill.cc
// TODO: create Makefile
#include <cassert>
#include <cstdio>
#include <cstring>
#include <ctime>

#include <new>

#include "src/debug_helpers.h"
#include "src/animation.h"
#include "src/slotsmm.h"

static const uint8_t nslots = 10;
uint8_t slots_data[nslots*MAX_ANIMATION_MEM];
uint8_t slots_usagev[nslots];


static const int n_pixels = 5;
hsv_t hsvbuf[n_pixels];
uint8_t strip_idx_arr[n_pixels];


int main()
{
  SlotsMM slotsmm(nslots, MAX_ANIMATION_MEM, slots_data, slots_usagev);
  PixelArray pa(n_pixels, hsvbuf, strip_idx_arr);
  pa.set_default_indexing();

  anim_params_t anim_params;
  anim_params.t_start = get_time_lf();
  anim_params.duration = 5.0;

  hsv_t init_color;
  init_color.h = 1.0;
  init_color.s = 2.0;
  init_color.v = 3.0;
  for (unsigned int i=0; i < pa.len(); i++) {
    pa[i] = init_color;
  }

  pa.print();
  fill_params_t fill_params;
  fill_params.color.h = 120.0;
  fill_params.color.s = 255.0;
  fill_params.color.v = 255.0;

  uint8_t params_buf[sizeof(anim_params_t) + sizeof(fill_params_t)];
  Animation *anim = new(slotsmm.allocate()) AnimationFill;

  assert(anim->header_size() == sizeof(anim_params_t) + sizeof(fill_params_t));
  memcpy(params_buf, &anim_params, sizeof(anim_params_t));
  memcpy(params_buf+sizeof(anim_params_t), &fill_params, sizeof(fill_params_t)); 

  printf("current timestamp: %lf\n", get_time_lf());  
  anim->setup(params_buf, sizeof(params_buf));
  anim->print(); 
  
  anim->render(0.0, pa);
  pa.print();

  return 0;
}