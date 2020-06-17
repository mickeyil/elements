#include <cstdint>

void hsv2rgb(int hue, int sat, int val, uint8_t rgbarr[3]) { 
  
  // http://www.kasperkamperman.com/blog/arduino/arduino-programming-hsb-to-rgb/
  
  int r;
  int g;
  int b;

  int base;

  if (sat == 0) { // Acromatic color (gray). Hue doesn't mind.
    rgbarr[0]=val;
    rgbarr[1]=val;
    rgbarr[2]=val;  
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

    rgbarr[0]=r;
    rgbarr[1]=g;
    rgbarr[2]=b; 
  }   
  
}