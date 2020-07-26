#pragma once

#include "animation_manager.h"
#include "renderable.h"


class Animation : public Renderable
{
  public:
    void render(float t_rel, float duration);
  
    void set(uint8_t* seqbuf, unsigned int len, const char **errstr);
    
  private:
    vec_u8 _code;
    AnimationManager *_panim_mgr;
};
