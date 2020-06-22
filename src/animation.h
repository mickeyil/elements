#pragma once

#include <cassert>
#include "pixelarray.h"

#ifdef DEBUG_HELPERS
#include <cstring>

#define MAX_DESCRIBE_STR  128
#endif

typedef enum {
    ANIMATION_TYPE_FILL = 1000,
} animation_type_t;

typedef enum {
  ANIMATION_STATE_PENDING = 0,
  ANIMATION_STATE_RUNNING,
  ANIMATION_STATE_DONE,
} animation_state_t;

typedef struct {
    double t_start;    // time is epoch in seconds.millisec resolution
    float duration;
} anim_params_t;

// maximum amount of memory usage for Animation class and subclasses.
// Through this constant 
#define MAX_ANIMATION_MEM 64

static const char* animation_state_str(animation_state_t state)
{
  switch (state) {
    case ANIMATION_STATE_PENDING:
      return "pending";
    case ANIMATION_STATE_RUNNING:
      return "running";
    case ANIMATION_STATE_DONE:
      return "done";
  }
  return "invalid";
}

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

    // returns sizeof(anim_params_t) + sizeof(internal struct of derived 
    // animation class). Any derived animation class must implement this as such.
    virtual uint32_t header_size() const = 0;

    // set duration to a smaller value
    void trim(double t_other) {
      if (_animation_params.t_start + _animation_params.duration > t_other) {
        _animation_params.duration = t_other - _animation_params.t_start;
      }
    }

    double t_start() const { return _animation_params.t_start; }
    float duration() const { return _animation_params.duration; }
    animation_state_t state() const { return _state; }

    #ifdef DEBUG_HELPERS
    // prints basic information about the animation (t_start, duration)
    virtual void print() { base_print(); }
    
    virtual const char * str() const = 0;
    #endif


  protected:
    void set_base_params(anim_params_t *pparams) {
      _animation_params = *pparams;
    }

    #ifdef DEBUG_HELPERS
    void base_print()
    {
      printf("Animation instance at %p:  t_start: %.3lf  duration: %.3f  state: %s\n",
        this, _animation_params.t_start, _animation_params.duration, 
        animation_state_str(_state));
        derived_print();
    }
    
    virtual void derived_print() = 0;
    #endif
    
    anim_params_t _animation_params;
    animation_state_t _state;
};


static_assert(sizeof(Animation) <= MAX_ANIMATION_MEM, "new MAX_ANIMATION_MEM value is needed.");


#include "animations/anim_fill.h"