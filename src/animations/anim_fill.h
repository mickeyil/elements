#pragma once

#include "animation.h"


typedef struct {
  hsv_t color;
} fill_params_t;


class AnimationFill : public Animation
{
  public:

    virtual uint32_t header_size() const {
      return sizeof(anim_params_t) + sizeof(fill_params_t);
    }

  protected:
    virtual void _setup(void *params, unsigned int size);

    virtual void _render(float t_rel, PixelArray& pa);

    #ifdef DEBUG_HELPERS
    virtual void derived_print() {
      printf("< %s\n", this->str());
    }

    virtual const char * str() const
    {
      static char sbuf[MAX_DESCRIBE_STR];
      snprintf(sbuf, MAX_DESCRIBE_STR, "Fill [HSV = (%3.1f,%3.1f,%3.1f)",
        _fill_params.color.h, _fill_params.color.s, _fill_params.color.v);
      return sbuf;
    }
    #endif

  private:
    fill_params_t _fill_params;
};

static_assert(sizeof(AnimationFill) <= MAX_ANIMATION_MEM, "new MAX_ANIMATION_MEM value is needed.");
