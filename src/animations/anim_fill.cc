#include <cassert>
#include "anim_fill.h"


void AnimationFill::_setup(void *params, unsigned int size, PixelArray& pa)
{
  DPRINTF("AnimationFill::_setup(): params=%p size=%u", params, size);
  if (_ppix_arr == nullptr) {
    DPRINTF("Creating new PixelArray.");
    _ppix_arr = new PixelArray(pa.len(), nullptr, nullptr);
  }
  
  DPRINTF("AnimationFill::_setup(): copy indexing to pa.");

  // copy indexing from given pixel array
  _ppix_arr->copy_indexing(pa);
  
  DPRINTF("AnimationFill::_setup(): done copy indexing.");

  fill_params_t *fill_params = (fill_params_t*) params;
  color = fill_params->color.to_hsv_t();
}


void AnimationFill::tear_down()
{
  if (_ppix_arr != nullptr) {
    delete _ppix_arr;
  }
}


void AnimationFill::_render(float t_rel, PixelArray& pa)
{
  if (duration() == 0.0) {
    // paint all leds with the same color
    for (unsigned int i = 0; i < pa.len(); i++) {
      pa[i] = color;
    }
  } else {
    float alpha = t_rel / duration();
    for (unsigned int i = 0; i < pa.len(); i++) {
      pa[i].h = (*_ppix_arr)[i].h + alpha * (color.h - (*_ppix_arr)[i].h);
      pa[i].s = (*_ppix_arr)[i].s + alpha * (color.s - (*_ppix_arr)[i].s);
      pa[i].v = (*_ppix_arr)[i].v + alpha * (color.v - (*_ppix_arr)[i].v);
    }
  }
}


void AnimationFill::_first_activation(PixelArray& pa)
{
  if (duration() > 0.0) {

    // copy pixel array on first activation to be used as bg
    for (unsigned int i = 0; i < pa.len(); i++) {
      (*_ppix_arr)[i] = pa[i];
    }
  }
}
