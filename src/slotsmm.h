#pragma once

#include <cstdint>
// simple slot based memory manager.
// using this avoids the needs for dynamic memory allocation within tha main
// program, in favor of one large allocation on setup() and allocation of fixed
// size memory slots inside that buffer.

class SlotsMM
{
  public:
    SlotsMM(unsigned int num_slots, unsigned int slot_size, 
      uint8_t* buffer=nullptr, uint8_t* usage_vec=nullptr);
    ~SlotsMM();

  unsigned int available_slots() const {
    return _avail_slots;
  }

  void * allocate();
  void deallocate(unsigned int slot_index);

  void * slot(unsigned int slot_index) {
    if (slot_index >= _num_slots) {
      return nullptr;
    }
    if (_usage_vec[slot_index] == 0) {
      return nullptr;
    } else {
      return _buffer + slot_index*_slot_size;
    }
  }

  private:
    uint8_t *_buffer;
    unsigned int _slot_size;
    unsigned int _num_slots;
    uint8_t *_usage_vec;
    unsigned int _avail_slots;
    bool _is_mem_owner;
};
