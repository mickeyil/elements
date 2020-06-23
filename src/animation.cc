#include "animation.h"


void Animation::setup(void *params, unsigned int size)
{
  anim_params_t *anim_params = reinterpret_cast<anim_params_t*>(params);
  set_base_params(anim_params);

  // call specific subclass setup
  _setup((uint8_t*) params + sizeof(anim_params_t), size-sizeof(anim_params_t));

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
  }

  if (t_rel >= _animation_params.duration) {
    _state = ANIMATION_STATE_DONE;
  }

  // call specific class rendering
  _render(t_rel, pa);
}
