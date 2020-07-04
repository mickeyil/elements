#include <cstdint>

static const uint8_t  gamma_correction_u8[] = 
{ 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 
  0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2, 
  2, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3, 4, 4, 4, 4, 4, 5, 5, 5, 5, 6, 6, 
  6, 6, 7, 7, 7, 7, 8, 8, 8, 9, 9, 9, 10, 10, 10, 11, 11, 11, 12, 12, 13, 13, 
  13, 14, 14, 15, 15, 16, 16, 17, 17, 18, 18, 19, 19, 20, 20, 21, 21, 22, 22, 
  23, 24, 24, 25, 25, 26, 27, 27, 28, 29, 29, 30, 31, 32, 32, 33, 34, 35, 35, 
  36, 37, 38, 39, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 50, 51, 52, 
  54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 66, 67, 68, 69, 70, 72, 73, 74, 
  75, 77, 78, 79, 81, 82, 83, 85, 86, 87, 89, 90, 92, 93, 95, 96, 98, 99,101,
  102,104,105,107,109,110,112,114, 115,117,119,120,122,124,126,127,129,131,133,
  135,137,138,140,142, 144,146,148,150,152,154,156,158,160,162,164,167,169,171,
  173,175, 177,180,182,184,186,189,191,193,196,198,200,203,205,208,210,213, 
  215,218,220,223,225,228,231,233,236,239,241,244,247,249,252,255 
};

#ifndef NO_GAMMA_CORRECTION
#define USE_GAMMA_CORRECTION
#endif

void hsv2rgb(int hue, int sat, int val, uint8_t& r_out, uint8_t& g_out, uint8_t& b_out)
{
  // http://www.kasperkamperman.com/blog/arduino/arduino-programming-hsb-to-rgb/
  
  int r=255;
  int g=255;
  int b=255;

  int base;

  #ifdef USE_GAMMA_CORRECTION
  val = gamma_correction_u8[val];
  #endif

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