#include "animation_manager.h"


void AnimationManager::setup_channel(void* setup_data_buf, unsigned int size, const char **errstr)
{
  channel_setup_header_t *channel_setup_header = (channel_setup_header_t*) setup_data_buf;
  
  strip_type_t strip_type = (strip_type_t) channel_setup_header->strip_type;
  if (strip_type != STRIP_RGB && strip_type != STRIP_BGR) {
    *errstr = "invalid strip type";
    return;
  }
  strip.set_type(strip_type);

  channel.setup_channel(setup_data_buf, size, errstr);
}


void AnimationManager::add_animation(void* setup_data_buf, unsigned int size, double t_abs_now, 
  const char **errstr)
{
  channel.add_animation(setup_data_buf, size,  t_abs_now, errstr);
}


void AnimationManager::render(double t_abs_now)
{
  channel.render(t_abs_now, strip);
}
