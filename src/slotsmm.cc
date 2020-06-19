#include <cassert>
#include <cstdlib>
#include <cstring>

#include "slotsmm.h"


SlotsMM::SlotsMM(unsigned int num_slots, unsigned int slot_size) :
  _buffer(nullptr), _slot_size(slot_size), _num_slots(num_slots), 
  _usage_vec(nullptr), _avail_slots(num_slots)
{
  // allocate main buffer and usage buffer
  _buffer = (uint8_t*) malloc(_slot_size * _num_slots);
  _usage_vec = (uint8_t*) malloc(_num_slots * sizeof(uint8_t));

  // initialize usage buffer. 0 means available for allocation.
  for (unsigned int i = 0; i < _num_slots; i++) {
    _usage_vec[i] = 0;
  }
}


SlotsMM::~SlotsMM()
{
  free(_buffer);
  free(_usage_vec);
}


void * SlotsMM::allocate()
{
  if (available_slots() == 0) {
    return nullptr;
  }

  // search for next available slot. expected number of slots to deal with is in
  // the 100's range, so a naive search is implemented for simplicity.
  for (unsigned int i = 0; i < _num_slots; i++) {
    if (_usage_vec[i] == 0) {
      void * ptr = _buffer + i*_slot_size;
      _usage_vec[i] = 1;
      _avail_slots--;
      return ptr;
    }
  }
  
  assert(0);    // should never get here - a slot must be found inside the loop.
  return nullptr;
}


void SlotsMM::deallocate(unsigned int slot_index)
{
  if (_usage_vec[slot_index] == 0) {
    // already deallocated
    return;
  }

  _usage_vec[slot_index] = 0;
  _avail_slots++;

  #ifdef TEST_SLOTSMM
  // for testing/validation
  void * ptr = _buffer + slot_index*_slot_size;
  bzero(ptr, _slot_size);
  #endif
}
