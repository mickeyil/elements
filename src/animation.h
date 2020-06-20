#pragma once

#include <cassert>
#include "pixelarray.h"

typedef enum {
    ANIMATION_CONSTANT = 1000,
    ANIMATION_BLEND,
} animation_type_t;

typedef enum {
  ANIMATION_STATE_PENDING = 0,
  ANIMATION_STATE_RUNNING,
  ANIMATION_STATE_DONE,
} animation_state_t;

typedef struct {
    double _t_start;    // time is epoch in seconds.millisec resolution
    float _duration;
} base_params_t;

// maximum amount of memory usage for Animation class and subclasses.
// Through this constant 
#define MAX_ANIMATION_MEM 64


class Animation
{
  public:

    // setup animation parameters.
    virtual void setup(void *params, unsigned int size) = 0;

    // render animation at relative time to animation time.
    // negative values case noop, since animation hasn't started yet.
    // values larger than duration time also immediately return.
    // animation is rendered on the given pixel array. It's size may be taken
    // into account in animation rendering.
    virtual void render(float t_rel, PixelArray& pa) = 0;
  
  protected:
    void set_base_params(base_params_t *pparams) {
      _animation_params = *pparams;
    }

    base_params_t _animation_params;
    animation_state_t _state;
};


static_assert(sizeof(Animation) <= MAX_ANIMATION_MEM, "new MAX_ANIMATION_MEM value is needed.");


#include "animations/anim_fill.h"