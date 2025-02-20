#pragma once

#include <cstdint>

#ifdef DEBUG_HELPERS
#include "debug_helpers.h"
#endif

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
    Strip(uint8_t *buf, uint16_t length, strip_type_t strip_type = STRIP_RGB) :
      _buf(buf), _len(length), _strip_type(strip_type) { }

    uint16_t len() const { return _len; }
    
    uint8_t * dataptr() { return _buf; }

    strip_type_t type() const { return _strip_type; }
    void set_type(strip_type_t strip_type) { _strip_type = strip_type; } 

    // direct access to pixel value
    const uint8_t& pixel(int pixel_index, pixel_color_t pixel_color) const;
          uint8_t& pixel(int pixel_index, pixel_color_t pixel_color);

    #ifdef DEBUG_HELPERS
    void print();
    const char * get_channel_str(unsigned int ch) const;
    #endif

  private:
    
    uint8_t *_buf;
    uint16_t _len;
    strip_type_t _strip_type;
};
