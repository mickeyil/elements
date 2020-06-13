// built with: g++ -g -Wall -o test_sample test_sample.cc
#include <cstdio>
#include <cassert>
#include <iostream>
#include "sample.h"

template <typename T, int N>
void print_samples(const Sample<T, N>& sample)
{
  std::cout << "print samples: ";
  for (int i = 0; i < N; i++) {
    std::cout << sample.get_item(i) << " ";
  }
  std::cout << std::endl;
}

int main()
{
  Sample<int, 5> sample(0);
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
  assert(sample.items_within_range(1,3) == 2);
  
  return 0;
}

