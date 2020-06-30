#pragma once

#include "animation.h"


typedef struct __attribute__((__packed__)) {
  float slope;
  hsvu8_t color;
} fill_params_t;


class AnimationFill : public Animation
{
  public:
    AnimationFill() : _ppix_arr(nullptr) { }
    virtual ~AnimationFill() { }
    virtual void tear_down();

    virtual uint32_t header_size() const {
      return sizeof(anim_params_t) + sizeof(fill_params_t);
    }

  protected:
    virtual void _setup(void *params, unsigned int size, PixelArray& pa);

    virtual void _render(float t_rel, PixelArray& pa);
    
    virtual void _first_activation(PixelArray& pa);
    
    #ifdef DEBUG_HELPERS
    virtual void derived_print() {
      printf("< %s\n", this->str());
    }

    virtual const char * str() const
    {
      static char sbuf[MAX_DESCRIBE_STR];
      snprintf(sbuf, MAX_DESCRIBE_STR, "Fill [FG = (%u,%u,%u) slope=%.3f]",
        _params.color.h, _params.color.s, _params.color.v, _params.slope);
      return sbuf;
    }
    #endif

  private:

    void f_activation_linear(hsv_t& pix_out, float t, const hsv_t& fg, const hsv_t& bg);

    fill_params_t _params;
    PixelArray *_ppix_arr;

    // cache of values used in rendering
    hsv_t _color;
    float _slope_coeff;   // 1 / slope
};

static_assert(sizeof(AnimationFill) <= MAX_ANIMATION_MEM, "new MAX_ANIMATION_MEM value is needed.");
