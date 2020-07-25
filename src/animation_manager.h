#pragma once
#include <cstdint>
#include <vector>

#include "strip.h"
#include "pixelarray.h"
#include "renderable.h"

typedef std::vector<uint8_t> vec_u8;

#define MAX_PIXEL_OBJECTS 32

class AnimationManager
{
  public:
    AnimationManager(Strip& strip) : _strip(strip) { }

    void execute(vec_u8& code, float progress, const char **errstr);

    void render(double t_abs_now, const char **errstr);

    std::vector<Renderable*> _renderables;

  private:
    Strip _strip;
    std::vector<PixelArray*> _pixel_objects;
};