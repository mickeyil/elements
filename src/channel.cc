#include <new>
#include "channel.h"
#include "utils.h"

#ifdef DEBUG_HELPERS
#include <cstdio>
#endif

// use custom allocation (SlotsMM) if given
#define ALLOCATE_INSTANCE(animvar, psmmvar, classtype) do { \
    if ((psmmvar) != nullptr) { \
      animvar = new(psmmvar->allocate()) classtype; \
    } else { \
      animvar = new classtype; \
    } \
  } while(0); 


Channel::Channel(SlotsMM *p_smm) :
  _ppix_arr(nullptr), _anim_timeline(ANIM_TIMELINE_MAX), _p_smm(p_smm)
{

}


Channel::~Channel()
{
  // clean up timeline resources
  if (_p_smm == nullptr) {
    while (_anim_timeline.size() > 0) {
      Animation *a = _anim_timeline.peek_from_tail(0);
      delete a;
      _anim_timeline.remove_last();
    }
  }
  if (_ppix_arr != nullptr) {
    delete _ppix_arr;
  }
}


void Channel::setup_channel(void* setup_data_buf, unsigned int size, const char **errstr)
{
  channel_setup_header_t *header = (channel_setup_header_t*) setup_data_buf;
  
  if (header->len + sizeof(channel_setup_header_t) != size) {
    *errstr = "invalid channel setup header";
    return;
  }

  uint8_t len = header->len;
  uint8_t *strip_idx_arr = ((uint8_t*) setup_data_buf) + sizeof(channel_setup_header_t);
  
  DPRINTF("setup_channel: len: %d, values: %s", len, arr2str(strip_idx_arr, len));

  // channel's PixelArray instance uses dynamic memory and copies the strip indices
  // from the given setup buffer. 
  if (_ppix_arr == nullptr) {
    _ppix_arr = new PixelArray(len, nullptr, strip_idx_arr);
    assert(_ppix_arr != nullptr);
  }

  // initialize pixelarray values to 0.0
  for (unsigned int i=0; i < len; i++) {
    (*_ppix_arr)[i] = hsv_t(0.0, 0.0, 0.0);
  }
}


void Channel::add_animation(void* setup_data_buf, unsigned int size,  double t_abs_now, 
  const char **errstr)
{
  // check for memory availability, in case of smm
  if (_p_smm != nullptr) {
    if (_p_smm->available_slots() == 0) {
      *errstr = "out of memory";
      return;
    }
  }

  if (_anim_timeline.size() == _anim_timeline.capacity()) {
    *errstr = "timeline queue is at max capacity";
    return;
  }

  DPRINTF("%s", arr2str((uint8_t *)setup_data_buf, size, true));

  anim_setup_header_t *setup_header = (anim_setup_header_t*) setup_data_buf;
  anim_params_t *anim_params = (anim_params_t*)((uint8_t*) setup_data_buf + sizeof(anim_setup_header_t));
  double t_end = anim_params->t_start + anim_params->duration;
  DPRINTF("t_abs_now: %lf  t_end: %lf", t_abs_now, t_end);
  if (t_abs_now > t_end && anim_params->duration > 0.0) {
    *errstr = "animation dropped: occurs in the past";
    return;
  }

  // create animation instance
  animation_type_t animation_type = (animation_type_t) setup_header->anim_type;
  Animation *animation = create_animation(animation_type);
  if (animation == nullptr) {
    *errstr = "invalid animation type";
    return;
  }

  unsigned int params_buf_size = size - sizeof(anim_setup_header_t);
  DPRINTF("data buf size: %u, sizeof(anim_setup_header_t): %u, animation->header_size(): %u",
    size, (unsigned int) sizeof(anim_setup_header_t), animation->header_size());
  DPRINTF("anim_params_t size: %u  fill_params_t: %u", (uint32_t) sizeof(anim_params_t),
    (uint32_t) sizeof(fill_params_t));
  if (animation->header_size() != params_buf_size) {
    *errstr = "invalid animation params size";
    return;
  }

  // initialize specific (derived) animation instance with animation parameters
  // channel's pixel array is passed here to enable the animation instance to 
  // create internal buffer with the same indexing.
  animation->setup(anim_params, params_buf_size, *_ppix_arr);
  #ifdef DEBUG_HELPERS
  printf("Channel::add_animation():\n");
  animation->print();
  #endif

  insert_to_timeline(animation, t_abs_now, true);
}


void Channel::insert_to_timeline(Animation* animation, double t_abs_now, bool trim_overlaps)
{
  _anim_timeline.push_front(animation);
  if (trim_overlaps) {
    // go over all previously inserted animations, trim animation durations
    // which their t_end > t_now to a new duration such that their t_end = t_now.
    for (int i = 0; i < static_cast<int>(_anim_timeline.size()) - 1; i++) {
      Animation *a = _anim_timeline.peek_from_tail(i);
      a->trim(animation->t_start());
    }
  }
}


Animation* Channel::create_animation(animation_type_t animation_type)
{
  Animation *animation = nullptr;

  switch(animation_type) {
    case ANIMATION_TYPE_FILL:
      ALLOCATE_INSTANCE(animation, _p_smm, AnimationFill);
      break;
  }

  return animation;
}


void Channel::render(double t_abs_now, Strip& strip)
{
  if(_ppix_arr == nullptr) {
    return;   // nothing to render on an uninitialized channel
  }
  timeline_cleanup(t_abs_now);
  
  for (unsigned int i = 0; i < _anim_timeline.size(); i++) {
    Animation *a = _anim_timeline.peek_from_tail(i);
    float t_rel = t_abs_now - a->t_start();
    a->render(t_rel, *_ppix_arr);
  }
  
  // write output to strip
  _ppix_arr->hsv_to_rgb_strip(strip);
}


void Channel::timeline_cleanup(double t_abs_now)
{
  while (_anim_timeline.size() > 0) {
    DPRINTF("timeline cleanup");
    Animation *a = _anim_timeline.peek_from_tail(0);
    if (a->state() == ANIMATION_STATE_DONE) {
      _anim_timeline.remove_last();
      a->tear_down();   // release resources taken by the animation instance

      if (_p_smm == nullptr) {
        delete a;
      }

    } else {
      break;    // timeline is sorted
    }
  }
}


#ifdef DEBUG_HELPERS
void Channel::print()
{
  if (_p_smm == nullptr) {
    printf("using dynamic memory allocation for animations.\n");
  } else {
    printf("using slots mm with %u slots of size %u.\n", _p_smm->num_slots(), 
      _p_smm->slot_size());
  }

  if (_ppix_arr == nullptr) {
    printf("pixel_array unallocated.\n");
  } else {
    printf("pixel_array:\n");
    _ppix_arr->print();
  }

  printf("timeline size: %u.\n", _anim_timeline.size());
  if (_anim_timeline.size() == 0) {
    return;
  }

  double t_now = get_time_lf();
  printf("timestamps below are displayed relative to current time: %16.3lf\n", t_now);
  printf("%-16s %-10s %-16s %-10s %s\n","t_start         ","duration  ","t_end           ","state     ","details");
  printf("%-16s %-10s %-16s %-10s %s\n","================","==========","================","==========","=======");
  

  for (unsigned int i = 0; i < _anim_timeline.size(); i++) {
    Animation *a = _anim_timeline.peek_from_tail(i);
    double t_start = a->t_start() - t_now;
    float duration = a->duration();
    double t_end = t_start + duration;
    const char *state = animation_state_str(a->state());
    const char *details = a->str();
    printf("%16.3lf %10.3f %16.3lf %-10s %s\n",
      t_start, duration, t_end, state, details);
  }
}
#endif
