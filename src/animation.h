#pragma once

#include "animation_manager.h"
#include "renderable.h"


class Animation : public Renderable
{
  public:
    void render(float t_rel, float duration);
  
  private:
    vec_u8 _code;
    AnimationManager *_panim_mgr;
};
