#pragma once

#include "animation.h"
#include "colors.h"


typedef struct {
  hsv_t color;
} fill_params_t;


class AnimationFill : public Animation
{
  public:
    AnimationFill();

    virtual void setup(void *params, unsigned int size);

    virtual void render(float t_rel, PixelArray& pa);

  private:
    fill_params_t _fill_params;
};

static_assert(sizeof(AnimationFill) <= MAX_ANIMATION_MEM, "new MAX_ANIMATION_MEM value is needed.");