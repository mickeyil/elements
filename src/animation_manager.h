#pragma once
#include <cstdint>

#include "channel.h"
#include "strip.h"

class AnimationManager
{
  public:
    AnimationManager(uint8_t *rgb_arr, uint16_t n_pixels) : strip(rgb_arr, n_pixels) { }

    void setup_channel(void* setup_data_buf, unsigned int size, const char **errstr);
    
    void add_animation(void* setup_data_buf, unsigned int size, double t_abs_now, 
      const char **errstr);

    void render(double t_abs_now);

  private:
    Strip strip;
    Channel channel;
};