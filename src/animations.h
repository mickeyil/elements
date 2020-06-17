#pragma once

#include <cstdint>

typedef struct {
  float h;
  float s;
  float v;
} hsv_t;


typedef struct {
  uint8_t r;
  uint8_t g;
  uint8_t b;
} rgb_t;


static const int MAX_LEDS = 150;

class Animation
{
  public:
    Animation(int _strip_len);
  
    void setup_uniform_fade(const hsv_t& _bg, const hsv_t& _fg, float _duration, long _t_start_ms);

    void render(long t_now_ms, uint8_t *rgbarr);
  
  private:
    hsv_t hsvarr[MAX_LEDS];
    int strip_len;

    unsigned long t_start_ms;
    float duration;
    hsv_t bg;
    hsv_t fg;
    float m;
};
