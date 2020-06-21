#include <cassert>
#include "anim_fill.h"


void AnimationFill::setup(void *params, unsigned int size)
{
  assert(size == sizeof(anim_params_t) + sizeof(fill_params_t));

  anim_params_t *anim_params = reinterpret_cast<anim_params_t*>(params);
  set_base_params(anim_params);

  fill_params_t *fill_params = reinterpret_cast<fill_params_t*>((uint8_t*)params + sizeof(anim_params_t));
  _fill_params = *fill_params;

  _state = ANIMATION_STATE_PENDING;
}


void AnimationFill::render(float t_rel, PixelArray& pa)
{
  if (t_rel < 0) {
    return;
  }

  if (_state == ANIMATION_STATE_PENDING) {
    _state = ANIMATION_STATE_RUNNING;
  }

  if (t_rel >= _animation_params.duration) {
    _state = ANIMATION_STATE_DONE;
  }

  for (unsigned int i = 0; i < pa.len(); i++) {
    pa[i] = _fill_params.color;
  }
}
