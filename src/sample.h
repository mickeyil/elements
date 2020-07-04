#pragma once

#include "writh.h"

template <typename T, int N>
class Sample
{
  public:
  Sample(T init_val) : head(0)
  {
    for (unsigned int i=0; i < N; i++) {
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

  unsigned int items_within_range(T low, T high, int history = N)
  {
    if (history > N) {
      history = N;
    }

    int idx = head - 1;
    if (idx < 0) {
      idx = N - 1;
    }

    unsigned int iwr = 0;
    for (unsigned int i = 0; i < (unsigned int) history; i++) {            
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
    for (unsigned int i = 0; i < history; i++) {
      temp_samples[i] = samples[i];
    }

    return median(temp_samples, history);
  }

  // for debugging/testing
  T get_item(unsigned int idx) const { return samples[idx]; }

  unsigned int size() const { return (unsigned int) N; }

  private:
  T samples[N];
  
  // head is the offset of the next place to populate in the ring buffer
  unsigned int head;

};
