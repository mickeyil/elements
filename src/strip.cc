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

#ifdef DEBUG_HELPERS
void Strip::print()
{
  if (_strip_type == STRIP_RGB) {
    print_title("R", "G", "B");
  } else if (_strip_type == STRIP_BGR) {
    print_title("B", "G", "R");
  } else {
    assert(0);  // invalid strip type
  }

  for (unsigned int i = 0; i < _len; i++) {
    uint8_t r = pixel(i, PIXEL_R);
    uint8_t g = pixel(i, PIXEL_G);
    uint8_t b = pixel(i, PIXEL_B);

    if (_strip_type == STRIP_RGB) {
      print_indexed_triplet<uint8_t>(i, r, g, b);
    } else if (_strip_type == STRIP_BGR) {
      print_indexed_triplet<uint8_t>(i, b, g, r);
    } 
  }
  printf("\n");
}
#endif
