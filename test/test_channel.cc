// g++ -g -DDEBUG_HELPERS -Wall -Wno-unused-function -I . -I src/ -o test/test_channel test/test_channel.cc src/slotsmm.cc src/pixelarray.cc src/strip.cc src/colors.cc src/animation.cc src/animations/anim_fill.cc src/channel.cc
// TODO: create Makefile!
#include <cassert>
#include <cstdio>
#include <cstring>

#include "channel.h"
#include "src/slotsmm.h"
#include "src/strip.h"

#include "src/debug_helpers.h"


static const unsigned int nslots = 10;


int main()
{
  printf("sizeof Animation: %zu\n", sizeof(Animation));
  printf("sizeof AnimationFill: %zu\n", sizeof(AnimationFill));
  printf("sizeof hsv_t is %zu\n", sizeof(hsv_t));
  // SlotsMM slotsmm(nslots, MAX_ANIMATION_MEM);
  Channel channel(nullptr);
  channel.print();

  double t_now = get_time_lf();

  anim_params_t anim_params;
  anim_params.t_start = t_now;
  anim_params.duration = 0.0;

  fill_params_t fill_params;
  fill_params.color.h = 120;
  fill_params.color.s = 255;
  fill_params.color.v = 255;

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

  channel_setup_header_t channel_setup_header;
  channel_setup_header.len = 5;
  channel_setup_header.strip_type = STRIP_RGB;
  uint8_t setup_buf[sizeof(channel_setup_header_t) + 5];
  
  memcpy(&setup_buf[0], &channel_setup_header, sizeof(channel_setup_header_t));
  memcpy((uint8_t*)setup_buf+sizeof(channel_setup_header_t), strip_idx_arr, 5);

  const char *errstr = nullptr;
  channel.setup_channel(setup_buf, sizeof(channel_setup_header_t) + 5, &errstr);
  if (errstr != nullptr) {
    printf("error: %s\n", errstr);
  }

  channel.print();

  

  channel.add_animation(params_buf, params_size, t_now, &errstr);
  if (errstr != nullptr) {
    printf("error: %s\n", errstr);
  }
  channel.print();

  anim_params.t_start = t_now + 0.1;
  anim_params.duration = 1.0;
  fill_params.color = hsvu8_t(120, 255, 0);
  
  memcpy(params_buf, &anim_setup_header, sizeof(anim_setup_header_t));
  memcpy(params_buf+sizeof(anim_setup_header_t), &anim_params, sizeof(anim_params_t));
  memcpy(params_buf+sizeof(anim_setup_header_t)+sizeof(anim_params_t), 
    &fill_params, sizeof(fill_params_t));

  channel.add_animation(params_buf, params_size, t_now, &errstr);
  if (errstr != nullptr) {
    printf("error: %s\n", errstr);
  }
  channel.print();

  #if 1
  uint8_t stripdata[100];
  memset(stripdata, 9, 100);
  
  Strip strip(stripdata, 15);
  strip.print();

  for (int i = 0; i < 13; i++) {
    channel.render(t_now + i*0.1, strip);
    strip.print();
    msleep(200);
  }
  #endif

  return 0;
}