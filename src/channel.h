#pragma once

#include "animation.h"
#include "slotsmm.h"
#include "strip.h"
#include "fixed_deque.h"

typedef struct {
  uint8_t len;
} channel_setup_header_t;

typedef struct {
  int32_t anim_type;    // values are animation_type_t enum int values
} anim_setup_header_t;

// max number of active animations in a timeline.
// animations which are done are removed from timeline.
#define ANIM_TIMELINE_MAX 10


class Channel
{
  public:
    Channel(SlotsMM *smm=nullptr);
    ~Channel();

    // setup channel properties. parameters are described in a header - 
    // (channel_setup_header_t), followed by an array of size 'len' [type uint8_t]
    // containing index mappings (strip_idx_arr) for pix_arr.
    void setup_channel(void* setup_data_buf, unsigned int size, const char **errstr);

    // add animation
    // setup_data_buf is: anim_setup_header_t + anim_params_t + <derived_class>_params_t
    void add_animation(void* setup_data_buf, unsigned int size, double t_abs_now, 
      const char **errstr);

    // channel render takes absolute time (epoch as double, with fractions for
    // milliseconds). render is made to a strip instance. 
    void render(double t_abs_now, Strip& strip);
  
    #ifdef DEBUG_HELPERS
    void print();
    #endif

  private:
    // factory for animation instances creation
    Animation* create_animation(animation_type_t animation_type);

    // insert animation instance to timeline.
    // NOTE: code assumes start time insertions occurs sorted! 
    // (i.e. sorted by the client).
    void insert_to_timeline(Animation* animation, double t_abs_now, bool trim_overlaps=true);

    PixelArray *_ppix_arr;  // dynamically allocated
    FixedDeque<Animation*>  _anim_timeline;
    SlotsMM *_p_smm;
};
