#include "animations.h"
#include "colors.h"

#include <Arduino.h>

Animation::Animation(int _strip_len) :
    strip_len(_strip_len)
{
    for (int i = 0; i < strip_len; i++) {
        hsvarr[i].h = 0.0;
        hsvarr[i].s = 0.0;
        hsvarr[i].v = 0.0;
    }
}

void Animation::setup_uniform_fade(const hsv_t& _bg, const hsv_t& _fg, float _duration, long _t_start_ms)
{
    duration = _duration;
    bg = _bg;
    fg = _fg;
    t_start_ms = _t_start_ms;
}


void Animation::render(long t_now_ms, uint8_t *rgbarr)
{    
    // current time [ms] relative to start time
    double t_rel = (static_cast<double>(t_now_ms) - static_cast<double>(t_start_ms)) / 1000.0;
    //Serial.print("t_start_ms: ");
    //Serial.print((double)t_start_ms);
    //Serial.print("\t t_now_ms: ");
    //Serial.println((double)t_now_ms);
    //Serial.println(t_rel);
    if (t_rel < 0) {
        //Serial.println("return..");
        return;
    }
    if (t_rel > duration) {
        t_start_ms = millis();
        return;
    }

    float alpha = t_rel / duration;

    for (int i = 0; i < strip_len; i++) {
        int val_h = bg.h + alpha * (fg.h - bg.h);
        int val_s = bg.s + alpha * (fg.s - bg.s);
        int val_v = bg.v + alpha * (fg.v - bg.v);

        hsv2rgb(val_h, val_s, val_v, &rgbarr[3*i]);
    }
}
