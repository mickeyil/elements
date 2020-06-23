#include <cassert>
#include "anim_fill.h"


void AnimationFill::_setup(void *params, unsigned int size)
{
  fill_params_t *fill_params = (fill_params_t*) params;
  _fill_params = *fill_params;
}


void AnimationFill::_render(float t_rel, PixelArray& pa)
{
  // paint all leds with the same color
  for (unsigned int i = 0; i < pa.len(); i++) {
    pa[i] = _fill_params.color;
  }
}
