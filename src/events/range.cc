#include <cassert>
#include <cstring>

#include "range.h"
#include "utils.h"


template <typename T>
bool in_range(T val, T min_val, T max_val)
{
    if (min_val <= val && val <= max_val)
        return true;
    return false;
}

// TODO: add error handling using errstr

void EventRange::_setup(const void *params, unsigned int size)
{
  assert(sizeof(range_params_t) == size);
  memcpy(&_range_params, params, size);

  DPRINTF("event range: min: %d max: %d", _range_params.min, _range_params.max);
}


bool EventRange::_condition(SamplingWindow<int16_t> *p_sampling_window)
{
  int val;
  if (p_sampling_window->size()>2) {
    val = p_sampling_window->get_median();
  } else {
    val = p_sampling_window->get_last_item();
  }

  bool confidence_factor = false;
  if (p_sampling_window->size() > 1 && _range_params.confidence < 1.0) {
    assert(_range_params.confidence > 0.0);   // FIXME: check when event registers
    int votes_threshold = static_cast<int>(_range_params.confidence * p_sampling_window->size());
    int votes = p_sampling_window->items_within_range(_range_params.min, _range_params.max);
    if (votes >= votes_threshold) {
      confidence_factor = true;
    }
  } else {
    confidence_factor = true;
  }

  bool cond = in_range((uint16_t) val, _range_params.min, _range_params.max);

  return cond && confidence_factor;
}
