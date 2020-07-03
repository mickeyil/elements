#include <cassert>
#include <cstdlib>

#include "pixelarray.h"


PixelArray::PixelArray(uint8_t len, hsv_t* hsv_ptr, uint8_t* sia_ptr)
  : _hsv_array(hsv_ptr), _len(len), _strip_idx_arr(sia_ptr), _is_hsvarr_memown(false), 
    _is_idxarr_memown(false)
{
  DPRINTF("PixelArray::PixelArray(). New array of length %u. hsv_ptr=%p, sia_ptr=%p",
    len, hsv_ptr, sia_ptr);

  // allocate memory if not provided
  // FIXME: handle memory allocation failures
  if (_hsv_array == nullptr) {
    _hsv_array = (hsv_t*) malloc(_len * sizeof(hsv_t));
    assert(_hsv_array != nullptr);
    _is_hsvarr_memown = true;
  }

  // TODO: eliminate those small frequent allocations
  _strip_idx_arr = (uint8_t*) malloc(_len*sizeof(uint8_t));
  assert(_strip_idx_arr != nullptr);
  _is_idxarr_memown = true;

  if (sia_ptr == nullptr) {  
    // initialize default mapping
    for (int i = 0; i < (int) _len; i++) {
      _strip_idx_arr[i] = i;
    } 
  } else {
    for (int i =0; i<len; i++) {
      _strip_idx_arr[i] = sia_ptr[i];
    }
  }
}


PixelArray::~PixelArray()
{
  if (_is_hsvarr_memown) {
    #ifdef DEBUG_HELPERS
    printf("~PixelArray(): freeing: %p\n", _hsv_array);
    #endif
    free(_hsv_array);
  }
  if (_is_idxarr_memown) {
    #ifdef DEBUG_HELPERS
    printf("~PixelArray(): freeing: %p\n", _strip_idx_arr);
    #endif
    free(_strip_idx_arr);
  }
}


void PixelArray::hsv_to_rgb_strip(Strip& rgb_strip)
{
  int len = (int) _len;
  if (len > (int) rgb_strip.len()) {
    len = (int) rgb_strip.len();
  }

  for (int i = 0; i < len; i++) {
    int pixel_index = _strip_idx_arr[i];
    uint8_t& r = rgb_strip.pixel(pixel_index, PIXEL_R);
    uint8_t& g = rgb_strip.pixel(pixel_index, PIXEL_G);
    uint8_t& b = rgb_strip.pixel(pixel_index, PIXEL_B);
    int hue = static_cast<int>(_hsv_array[i].h);
    int sat = static_cast<int>(_hsv_array[i].s);
    int val = static_cast<int>(_hsv_array[i].v);
    
    hsv2rgb(hue, sat, val, r, g, b);
  }
}


#ifdef DEBUG_HELPERS
static const char *color_space_str[2][3] = {
  {"H", "S", "V"},
  {"R", "G", "B"},
};


void PixelArray::print()
{
  print_pa_title("H", "S", "V");
  for (unsigned int i = 0; i < _len; i++) {
    print_pa_values(i, _strip_idx_arr[i], _hsv_array[i].h, _hsv_array[i].s, _hsv_array[i].v);
  }
  printf("\n");
}


void PixelArray::pprint(double t_start, double t_abs_now, bool convert_to_rgb)
{
  unsigned int cs_select;
  if (convert_to_rgb) {
    cs_select = 1;
  } else {
    cs_select = 0;
  }

  printf("T+%5.3lf  (%.3lf)\n", t_start, t_abs_now);
  printf("   ");
  // print title
  for (unsigned int i = 0; i < _len; i++) {
    printf("%3d ", _strip_idx_arr[i]);
  }
  printf("\n   ");
  for (unsigned int i = 0; i < _len; i++) {
    printf("=== ");
  }
  printf("\n");

  for (unsigned int ch = 0; ch < 3; ch++) {
    printf("%s: ", color_space_str[cs_select][ch]);
    for (unsigned int i = 0; i < _len; i++) {
      unsigned int print_val;
      int hsv[3];
      hsv[0] = static_cast<int>(_hsv_array[i].h);
      hsv[1] = static_cast<int>(_hsv_array[i].s);
      hsv[2] = static_cast<int>(_hsv_array[i].v);
      
      if (convert_to_rgb) {
        uint8_t rgb[3];
        hsv2rgb(hsv[0], hsv[1], hsv[2], rgb[0], rgb[1], rgb[2]);
        print_val = rgb[ch];
      } else {
        print_val = hsv[ch];
      }
      
      printf("%3d ", print_val);
    }
    printf("\n");
  }
  printf("\n");
  fflush(stdout);
}

#endif
