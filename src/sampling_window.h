#pragma once

#include <vector>

// for calculating median
#include "writh.h"

template <typename T>
class SamplingWindow
{
  public:
  SamplingWindow(unsigned int size, const T& init_val) : _samples(size, init_val), _head(0)
    { }

  // sample single value
  void sample(const T& value)
  {
    _samples[_head] = value;
    _head += 1;
    if (_head == _samples.size()) {
      _head = 0;
    }
  }

  // returns the number of items in the sample window that are between low and high
  // looks no further than history_window elements (default: all sample window)
  unsigned int items_within_range(T low, T high, unsigned int history_window)
  {
    unsigned int N = _samples.size();
    if (history_window > N) {
      history_window = N;
    }

    int idx = _head - 1;
    if (idx < 0) {
      idx = N - 1;
    }

    unsigned int iwr = 0;
    for (unsigned int i = 0; i < history_window; i++) {            
      if (low <= _samples[idx] && _samples[idx] <= high) {
        iwr += 1;
      }
      
      idx -= 1;
      if (idx < 0) {
        idx = N - 1;
      }
    }
    return iwr;
  }

  unsigned int items_within_range(T low, T high)
  {
    return items_within_range(low, high, _samples.size());
  }

  T get_median(int history) const
  {
    unsigned int N = _samples.size();
    if (history > N) {
      history = N;
    }

    // copy samples to a temporary array for median algorithm (modifies in place)
    std::vector<T> temp_samples(_samples);

    return median(&temp_samples[0], history);
  }

  // for debugging/testing
  T get_item(unsigned int idx) const { return _samples[idx]; }

  unsigned int size() const { return _samples.size(); }

  private:
  std::vector<T> _samples;
  
  // head is the offset of the next place to populate in the ring buffer
  unsigned int _head;

};
