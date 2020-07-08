// built with: g++ -g -Wall -o test_sample test_sample.cc
#include <cstdio>
#include <cassert>
#include <iostream>
#include "sampling_window.h"

template <typename T>
void print_samples(const SamplingWindow<T>& sample)
{
  std::cout << "print samples: ";
  for (unsigned int i = 0; i < sample.size(); i++) {
    std::cout << sample.get_item(i) << " ";
  }
  std::cout << std::endl;
}

int main()
{
  SamplingWindow<int> sample(15, 0);
  print_samples(sample);
  sample.sample(9);
  print_samples(sample);
  sample.sample(1);
  print_samples(sample);
  sample.sample(2);
  print_samples(sample);
  sample.sample(15);
  print_samples(sample);
  sample.sample(1);
  print_samples(sample);
  assert(sample.items_within_range(1,3) == 3);
  sample.sample(90);
  print_samples(sample);
  sample.sample(190);
  print_samples(sample);
  assert(sample.items_within_range(1,3,3) == 1);
  
  return 0;
}

