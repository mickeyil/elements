#pragma once

#include "animation.h"
#include "colors.h"


typedef struct {
  hsv_t color;
} fill_params_t;


class AnimationFill : public Animation
{
  public:

    virtual void setup(void *params, unsigned int size);

    virtual void render(float t_rel, PixelArray& pa);

  protected:

    #ifdef DEBUG_HELPERS
    virtual void derived_print() {
      printf("< AnimationFill: fill params: H: %.1f S: %.1f V: %.1f\n",
        _fill_params.color.h, _fill_params.color.s, _fill_params.color.v);
    }
    #endif

  private:
    fill_params_t _fill_params;
};

static_assert(sizeof(AnimationFill) <= MAX_ANIMATION_MEM, "new MAX_ANIMATION_MEM value is needed.");