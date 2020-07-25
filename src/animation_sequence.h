#pragma once

#include <cstdint>
#include <vector>

#include "renderable.h"
#include "animation_manager.h"

typedef struct __attribute__((__packed__)) {
  float t_start;
  float duration;
  uint8_t idx;
} anim_seq_t;


class AnimationSequence : public Renderable
{
  public:
    AnimationSequence(AnimationManager *panim_mgr) : 
      _panim_mgr(panim_mgr), _next_anim_idx(0) { }
    
    virtual ~AnimationSequence() { }

    void render(float t_rel, float duration);

    void set(uint8_t* seqbuf, unsigned int len, const char **errstr);

  private:
    AnimationManager *_panim_mgr;
    std::vector<anim_seq_t> _anim_vec;
    uint8_t _next_anim_idx;
};
