// g++ -g -DDEBUG_HELPERS -Wall -Wno-unused-function -I . -I src/ -o test/test_channel test/test_channel.cc src/slotsmm.cc src/pixelarray.cc src/strip.cc src/colors.cc src/animation.cc src/animations/anim_fill.cc src/channel.cc
// TODO: create Makefile!
#include <cassert>
#include <cstdio>
#include <cstring>

#include "channel.h"
#include "src/slotsmm.h"

#include "src/debug_helpers.h"


static const unsigned int nslots = 10;


int main()
{
  printf("sizeof Animation: %zu\n", sizeof(Animation));
  printf("sizeof AnimationFill: %zu\n", sizeof(AnimationFill));
  printf("sizeof hsv_t is %zu\n", sizeof(hsv_t));
  SlotsMM slotsmm(nslots, MAX_ANIMATION_MEM);
  Channel channel(&slotsmm);
  channel.print();

  anim_params_t anim_params;
  anim_params.t_start = get_time_lf();
  anim_params.duration = 500.0;

  fill_params_t fill_params;
  fill_params.color.h = 120.0;
  fill_params.color.s = 255.0;
  fill_params.color.v = 255.0;

  anim_setup_header_t anim_setup_header;
  anim_setup_header.anim_type = ANIMATION_TYPE_FILL;

  unsigned int params_size = sizeof(anim_setup_header_t) + sizeof(anim_params_t) + sizeof(fill_params_t);

  uint8_t params_buf[params_size];

  memcpy(params_buf, &anim_setup_header, sizeof(anim_setup_header_t));
  memcpy(params_buf+sizeof(anim_setup_header_t), &anim_params, sizeof(anim_params_t));
  memcpy(params_buf+sizeof(anim_setup_header_t)+sizeof(anim_params_t), 
    &fill_params, sizeof(fill_params_t));

  uint8_t strip_idx_arr[5] = {10, 11, 12, 13, 14};
  channel_setup_header_t setup_header;
  setup_header.len = 5;

  printf("size of setup_header is: %zu\n", sizeof(setup_header));

  uint8_t setup_buf[6];
  setup_buf[0] = 5;
  memcpy(&setup_buf[1], strip_idx_arr, 5);

  const char *errstr = nullptr;
  channel.setup_channel(setup_buf, 6, &errstr);
  if (errstr != nullptr) {
    printf("error: %s\n", errstr);
  }

  channel.print();

  double t_now = get_time_lf();
  

  channel.add_animation(params_buf, params_size, t_now, &errstr);
  if (errstr != nullptr) {
    printf("error: %s\n", errstr);
  }
  channel.print();

  anim_params.t_start = get_time_lf() + 0.2;
  anim_params.duration = 1.0;
  fill_params.color = hsv_t(125.0, 125.0, 125.0);
  
  memcpy(params_buf, &anim_setup_header, sizeof(anim_setup_header_t));
  memcpy(params_buf+sizeof(anim_setup_header_t), &anim_params, sizeof(anim_params_t));
  memcpy(params_buf+sizeof(anim_setup_header_t)+sizeof(anim_params_t), 
    &fill_params, sizeof(fill_params_t));

  channel.add_animation(params_buf, params_size, t_now, &errstr);
  if (errstr != nullptr) {
    printf("error: %s\n", errstr);
  }
  channel.print();


  return 0;
}