#pragma once

#include "writh.h"

template <typename T, int N>
class Sample
{
  public:
  Sample(T init_val) : head(0)
  {
    for (size_t i=0; i < N; i++) {
      samples[i] = init_val;
    }
  }

  void sample(T value)
  {
    samples[head] = value;
    head += 1;
    if (head == N) {
      head = 0;
    }
  }

  size_t items_within_range(T low, T high, int history = N)
  {
    if (history > N) {
      history = N;
    }

    int idx = head - 1;
    if (idx < 0) {
      idx = N - 1;
    }

    size_t iwr = 0;
    for (size_t i = 0; i < (size_t) history; i++) {            
      if (low <= samples[idx] && samples[idx] <= high) {
        iwr += 1;
      }
      
      idx -= 1;
      if (idx < 0) {
        idx = N - 1;
      }
    }
    return iwr;
  }

  T get_median(int history = N) const
  {
    if (history > N) {
      history = N;
    }

    // copy samples to a temporary array for median algorithm (modifies in place)
    T temp_samples[N];
    for (size_t i = 0; i < history; i++) {
      temp_samples[i] = samples[i];
    }

    return median(temp_samples, history);
  }

  // for debugging/testing
  T get_item(size_t idx) const { return samples[idx]; }

  size_t size() const { return (size_t) N; }

  private:
  T samples[N];
  
  // head is the offset of the next place to populate in the ring buffer
  size_t head;

};
