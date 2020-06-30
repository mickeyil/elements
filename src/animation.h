#pragma once

#include <cassert>
#include "colors.h"
#include "pixelarray.h"

#include "utils.h"

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

typedef struct __attribute__((__packed__)) {
    double t_start;    // time is epoch in seconds.millisec resolution
    float duration;
} anim_params_t;

// maximum amount of memory usage for Animation class and subclasses.
// Through this constant 
#define MAX_ANIMATION_MEM 96

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
    virtual ~Animation() { }

    // setup animation parameters.
    void setup(void *params, unsigned int size, PixelArray& pa);

    // render animation at relative time to animation time.
    // negative values case noop, since animation hasn't started yet.
    // values larger than duration time also immediately return.
    // animation is rendered on the given pixel array. It's size may be taken
    // into account in animation rendering.
    void render(float t_rel, PixelArray& pa);

    // allow the  animation object to release resources
    virtual void tear_down() { }

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

    // setup specific animation parameters
    virtual void _setup(void *params, unsigned int size, PixelArray& pa) = 0;

    // specific implemetation of animation rendering is done in subclasses,
    // in the following function implementation.
    virtual void _render(float t_rel, PixelArray& pa) = 0;

    // called on first activation. allows for copying pixel array values at
    // the time of first activation. default implementation is a no-op.
    virtual void _first_activation(PixelArray& pa) { }

    void set_base_params(anim_params_t *pparams) {
      _animation_params = *pparams;
      DPRINTF("Animation::set_base_params(): t_start=%lf, duration=%f", 
        _animation_params.t_start, _animation_params.duration);
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