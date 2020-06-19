#include "strip.h"

static int offsets[2][3] = {
  {0, 1 ,2},  // RGB
  {2, 1, 0},  // BGR
};


const uint8_t& Strip::pixel(int pixel_index, pixel_color_t pixel_color) const
{
  uint8_t* base_ptr = &_buf[3*pixel_index];
  
  // use enum mappings of strip type and given pixel color to determine the
  // correct offset. buffer is always in layout 'RGB'.
  int offset = offsets[static_cast<int>(_strip_type)][static_cast<int>(pixel_color)];
  return *(base_ptr + offset);
}

uint8_t& Strip::pixel(int pixel_index, pixel_color_t pixel_color)
{
  return const_cast<uint8_t&>(const_cast<const Strip*>(this)->pixel(pixel_index, pixel_color));
}
