#pragma once

#include <cstdint>

#include "colors.h"
#include "strip.h"

#ifdef DEBUG_HELPERS
#include "debug_helpers.h"
#endif


class PixelArray
{
  public:
    PixelArray(uint8_t len, hsv_t* hsv_ptr=nullptr, uint8_t* sia_ptr=nullptr);
    ~PixelArray();

    const hsv_t& operator[](unsigned int idx) const {
      return _hsv_array[idx];
    }

          hsv_t& operator[](unsigned int idx)       {
      return _hsv_array[idx];
    }

    unsigned int len() const { return _len; }

    // convert hsv to rgb and assign to an rgb strip instance according to
    // indexing
    void hsv_to_rgb_strip(Strip& rgb_strip);

    #ifdef DEBUG_HELPERS
    void print();
    #endif

  private:
    hsv_t    *_hsv_array;
    uint8_t   _len;
    uint8_t  *_strip_idx_arr;
    
    // memory ownership indicators
    bool _is_hsvarr_memown;
    bool _is_idxarr_memown;
};
