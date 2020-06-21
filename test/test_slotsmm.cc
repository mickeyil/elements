// g++ -g -DTEST_SLOTSMM -Wall -I . -o test/test_slotsmm test/test_slotsmm.cc src/slotsmm.cc
#include <cassert>
#include <cstdio>
#include <cstdint>

#include "src/slotsmm.h"

#ifndef DEBUG_HELPERS
# error "Test should be built with SLOTSMM defined"
#endif

typedef struct {
  uint32_t a;
  uint32_t b;
  uint32_t c;
} num_t;

#define BUF_N_SIZE  3
uint8_t databuf[sizeof(num_t)*BUF_N_SIZE];
uint8_t usagebuf[BUF_N_SIZE];

int main()
{
  SlotsMM slotsmm1(3, sizeof(num_t), databuf, usagebuf);
  num_t n1;
  n1.a = 10;
  n1.b = 20;
  n1.c = 30;

  assert(slotsmm1.available_slots() == 3);
  
  num_t* pn = (num_t*) slotsmm1.allocate();
  *pn = n1;

  printf("first slot: %p\n", pn);

  assert(pn->a == 10);
  num_t* pn2 = (num_t*) slotsmm1.slot(1);
  assert(pn2 == nullptr);
  pn2 = (num_t*) slotsmm1.slot(0);
  assert(pn2 != nullptr);

  assert(pn2->b == 20);
  assert(slotsmm1.available_slots() == 2);
  assert(pn2 == pn);
  
  pn2 = (num_t*) slotsmm1.allocate();
  printf("second slot: %p\n", pn2);
  assert(slotsmm1.available_slots() == 1);
  assert(pn2 != pn);
  pn2->a = 1;
  pn2->b = 2;
  pn2->c = 3;

  num_t* pn3 = (num_t*) slotsmm1.allocate();
  printf("second slot: %p\n", pn3);
  assert(pn3 != pn2);

  *pn3 = *pn2;
  assert(pn3->a == 1);

  assert(slotsmm1.available_slots() == 0);
  num_t* pn4 = (num_t*)slotsmm1.allocate();
  assert(pn4 == nullptr);

  slotsmm1.deallocate(1);
  assert(slotsmm1.available_slots() == 1);
  num_t* pn2b = (num_t*)slotsmm1.allocate();
  assert(pn2b == pn2);

  slotsmm1.deallocate(1);
  assert(slotsmm1.slot(0) == pn);
  assert(slotsmm1.slot(1) == nullptr);
  assert(slotsmm1.slot(2) == pn3);
  assert(slotsmm1.slot(3) == nullptr);

  return 0;
}