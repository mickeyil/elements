#include <cassert>
#include <cstdlib>
#include <cstring>

#include "slotsmm.h"

#ifdef DEBUG_HELPERS
#include <cstdio>
#endif


SlotsMM::SlotsMM(unsigned int num_slots, unsigned int slot_size,
  uint8_t* buffer, uint8_t* usage_vec) :
  _buffer(buffer), _slot_size(slot_size), _num_slots(num_slots), 
  _usage_vec(usage_vec), _avail_slots(num_slots), _is_mem_owner(false)
{
  if (buffer == nullptr) {
    // allocate main buffer and usage buffer
    _buffer = (uint8_t*) malloc(_slot_size * _num_slots);
    _usage_vec = (uint8_t*) malloc(_num_slots * sizeof(uint8_t));
    _is_mem_owner = true;
  }

  // initialize usage buffer. 0 means available for allocation.
  for (unsigned int i = 0; i < _num_slots; i++) {
    _usage_vec[i] = 0;
  }
}


SlotsMM::~SlotsMM()
{
  if (_is_mem_owner) {
    #ifdef DEBUG_HELPERS
    printf("~SlotsMM(): freeing: %p and %p\n", _buffer, _usage_vec);
    #endif
    free(_buffer);
    free(_usage_vec);
  }
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
      
      #ifdef DEBUG_HELPERS
      printf("SlotsMM::allocate(): idx: %u  -- avail: %u --  %p\n", i, _avail_slots, ptr);
      #endif

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

  #ifdef DEBUG_HELPERS
  printf("SlotsMM::deallocate(): idx: %u -- avail: %u\n", slot_index, _avail_slots);
  #endif

  #ifdef DEBUG_HELPERS
  // for testing/validation
  void * ptr = _buffer + slot_index*_slot_size;
  bzero(ptr, _slot_size);
  #endif
}
