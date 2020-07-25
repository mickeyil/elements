#include <cassert>
#include <cstring>

#include "animation_sequence.h"


void AnimationSequence::render(float t_rel, float duration)
{
  assert(t_rel >= 0 && t_rel <= duration);
  anim_seq_t anim_params;

  for (unsigned int i = _next_anim_idx; i < _anim_vec.size(); i++) {
    // copying done to avoid memory alignment issues
    memcpy(&anim_params, &_anim_vec[i], sizeof(anim_seq_t));
      
    assert (anim_params.idx < _panim_mgr->_renderables.size());
    Renderable *renderable = _panim_mgr->_renderables[anim_params.idx];
    assert(renderable != nullptr);

    // consume all animations with duration of 0.0
    if (anim_params.duration == 0.0) {
      renderable->render(0.0, 0.0);
      _next_anim_idx++;
      continue;
    }

    // skip animations which render time has passed
    if (anim_params.t_start + anim_params.duration < t_rel) {
      _next_anim_idx++;
      continue;
    }

    // skip animations which render start time is in the future
    if (t_rel < anim_params.t_start) {
      return;
    }

    // remaining case is an animation with durations > 0 and t_rel is in its
    // render time window
    assert(anim_params.t_start <= t_rel &&
           t_rel <= anim_params.t_start + anim_params.duration);
    
    // render with time value relative to the animation start time
    float t_rel_anim = t_rel - anim_params.t_start;
    renderable->render(t_rel_anim, anim_params.duration);
  }
}


void AnimationSequence::set(uint8_t* seqbuf, unsigned int len, const char **errstr)
{
  assert(_anim_vec.size() == 0 && _next_anim_idx == 0);
  if (len % sizeof(anim_seq_t) != 0) {
    *errstr = "invalid animation sequence buffer size";
    return;
  }
  
  // set
  unsigned int n_anim = len / sizeof(anim_seq_t);
  _anim_vec.resize(n_anim);
  memcpy(&_anim_vec[0], seqbuf, len);

  // validate
  anim_seq_t anim_params;
  for (unsigned int i = 0; i < _anim_vec.size(); i++) {
    
    // copying done to avoid memory alignment issues
    memcpy(&anim_params, &_anim_vec[i], sizeof(anim_seq_t));

    if (anim_params.t_start < 0.0 || anim_params.t_start > 1e6) {
      *errstr = "t_start oob";
      return;
    }

    if (anim_params.duration < 0.0 || anim_params.duration > 1e6) {
      *errstr = "duration oob";
      return;
    }

    if (anim_params.idx >= _panim_mgr->_renderables.size()) {
      *errstr = "renderables idx out of range";
      return;
    }

    if (_panim_mgr->_renderables[anim_params.idx] == nullptr) {
      *errstr = "null renderable object";
      return;
    }
  }
}
