#pragma once

class Renderable
{
  public:
    virtual ~Renderable();
    virtual void render(float t_rel, float duration) = 0;
};
