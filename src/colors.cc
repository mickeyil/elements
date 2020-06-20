#include <cstdint>

void hsv2rgb(int hue, int sat, int val, uint8_t& r_out, uint8_t& g_out, uint8_t& b_out)
{
  // http://www.kasperkamperman.com/blog/arduino/arduino-programming-hsb-to-rgb/
  
  int r;
  int g;
  int b;

  int base;

  if (sat == 0) { // Acromatic color (gray). Hue doesn't mind.
    r_out=static_cast<uint8_t>(val);
    g_out=static_cast<uint8_t>(val);
    b_out=static_cast<uint8_t>(val);  
  } else  { 
    base = ((255 - sat) * val)>>8;

    switch(hue/60) {
    case 0:
        r = val;
        g = (((val-base)*hue)/60)+base;
        b = base;
    break;
    case 1:
        r = (((val-base)*(60-(hue%60)))/60)+base;
        g = val;
        b = base;
    break;
    case 2:
        r = base;
        g = val;
        b = (((val-base)*(hue%60))/60)+base;
    break;
    case 3:
        r = base;
        g = (((val-base)*(60-(hue%60)))/60)+base;
        b = val;
    break;
    case 4:
        r = (((val-base)*(hue%60))/60)+base;
        g = base;
        b = val;
    break;
    case 5:
        r = val;
        g = base;
        b = (((val-base)*(60-(hue%60)))/60)+base;
    break;
    }

    r_out=static_cast<uint8_t>(r);
    g_out=static_cast<uint8_t>(g);
    b_out=static_cast<uint8_t>(b); 
  }   
  
}