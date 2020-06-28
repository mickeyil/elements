#include "animation.h"


void Animation::setup(void *params, unsigned int size, PixelArray& pa)
{
  anim_params_t *anim_params = reinterpret_cast<anim_params_t*>(params);
  DPRINTF("Animation::setup(): params=%p size=%u", params, size);
  set_base_params(anim_params);

  // call specific subclass setup
  _setup((uint8_t*) params + sizeof(anim_params_t), size-sizeof(anim_params_t), pa);

  _state = ANIMATION_STATE_PENDING;
}


void Animation::render(float t_rel, PixelArray& pa)
{
  // nothing to render if current time is before render time
  if (t_rel < 0) {
    return;
  }

  if (_state == ANIMATION_STATE_PENDING) {
    _state = ANIMATION_STATE_RUNNING;
    _first_activation(pa);
  }

  // call specific class rendering
  _render(t_rel, pa);
  
  if (t_rel >= _animation_params.duration) {
    _state = ANIMATION_STATE_DONE;
  }
}

