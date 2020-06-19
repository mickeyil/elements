#pragma once

#include <cstdint>


typedef enum {
  STRIP_RGB = 0,
  STRIP_BGR,
} strip_type_t;

typedef enum {
  PIXEL_R = 0,
  PIXEL_G,
  PIXEL_B,
} pixel_color_t;

class Strip
{
  public:
    Strip(uint8_t *_buf, uint16_t _length, strip_type_t _strip_type = STRIP_RGB) :
      buf(_buf), len(_length), strip_type(_strip_type) { }

    uint16_t get_len() const { return len; }
    
    uint8_t * dataptr()       { return buf; }

    strip_type_t type() const { return strip_type; }

    // direct access to pixel value
    const uint8_t& pixel(int pixel_index, pixel_color_t pixel_color) const;
          uint8_t& pixel(int pixel_index, pixel_color_t pixel_color);

  private:
    
    uint8_t *buf;
    uint16_t len;
    strip_type_t strip_type;
};
