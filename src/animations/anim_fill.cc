#include <cassert>
#include <cstring>
#include "anim_fill.h"


void AnimationFill::_setup(void *params, unsigned int size, PixelArray& pa)
{
  assert(sizeof(fill_params_t) == size);
  DPRINTF("AnimationFill::_setup(): params=%p size=%u", params, size);
  if (_ppix_arr == nullptr) {
    DPRINTF("Creating new PixelArray.");
    _ppix_arr = new PixelArray(pa.len(), nullptr, nullptr);
  }
  
  DPRINTF("AnimationFill::_setup(): copy indexing to pa.");

  // copy indexing from given pixel array
  _ppix_arr->copy_indexing(pa);
  
  DPRINTF("AnimationFill::_setup(): done copy indexing.");
  
  // copy parameters
  memcpy(&_params, params, size);
  
  _color = _params.color.to_hsv_t();
  _slope_coeff = -1.0 / _params.slope;
  _time_coeff = _params.speed_factor / duration();
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
      pa[i] = _color;
    }
  } else {
    if (_params.slope == 0.0) {
      float alpha = t_rel / duration();
      for (unsigned int i = 0; i < pa.len(); i++) {
        pa[i].h = (*_ppix_arr)[i].h + alpha * (_color.h - (*_ppix_arr)[i].h);
        pa[i].s = (*_ppix_arr)[i].s + alpha * (_color.s - (*_ppix_arr)[i].s);
        pa[i].v = (*_ppix_arr)[i].v + alpha * (_color.v - (*_ppix_arr)[i].v);
      }
    } else {
      for (unsigned int i =0; i < pa.len(); i++) {
        float t = static_cast<float>(i)/(pa.len()-1) - t_rel * _time_coeff;
        f_activation_linear(pa[i], t, _color, (*_ppix_arr)[i]);
      }
    }
  }
}


void AnimationFill::f_activation_linear(hsv_t& pix_out, float t, const hsv_t& fg, const hsv_t& bg)
{
  if (t >= 0.0) {
    pix_out = bg;
  } else if (t <= _slope_coeff) {    // t < -1.0/slope
    pix_out = fg;
  } else {  // -1.0/slope < t < 0.0 
    float alpha = -_params.slope * t;
    pix_out.h = bg.h + alpha*(fg.h - bg.h);
    pix_out.s = bg.s + alpha*(fg.s - bg.s);
    pix_out.v = bg.v + alpha*(fg.v - bg.v);
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
