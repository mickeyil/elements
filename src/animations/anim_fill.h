#pragma once

#include "animation.h"


typedef struct {
  hsvu8_t color;
} fill_params_t;


class AnimationFill : public Animation
{
  public:
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
      snprintf(sbuf, MAX_DESCRIBE_STR, "Fill [HSV = (%3.1f,%3.1f,%3.1f)",
        color.h, color.s, color.v);
      return sbuf;
    }
    #endif

  private:
    hsv_t color;
    PixelArray *_ppix_arr;
};

static_assert(sizeof(AnimationFill) <= MAX_ANIMATION_MEM, "new MAX_ANIMATION_MEM value is needed.");
