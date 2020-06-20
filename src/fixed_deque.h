#pragma once

#include <cstdlib>

template <typename T>
class FixedDeque
{
  public:
    FixedDeque(unsigned int capacity, T* databuf=nullptr);
    ~FixedDeque();

    unsigned int capacity() const { return _capacity; }
    unsigned int size() const { return _size; }

    void push_front(const T& elem);
    void remove_last();   // no value is returned. peek is used to read data
    
    // peek returns a reference to an item indexed relative to the tail pointer
    // mean for simple scanning method from "oldest" (0) to "newest" (size-1).
    const T& peek_from_tail(unsigned int idx_rel) const;
          T& peek_from_tail(unsigned int idx_rel);

  private:
    T* _databuf;
    bool _is_mem_owner;       // memory ownership indicator
    unsigned int _size;       // number of elements currely in the deque
    unsigned int _capacity;   // buffer size
    unsigned int _head_idx;   // index for the next place to insert to
    unsigned int _tail_idx;   // index of the "oldest" item
};


template <typename T>
FixedDeque<T>::FixedDeque(unsigned int capacity, T* databuf)
  : _databuf(databuf), _is_mem_owner(false), _size(0), _capacity(capacity), 
  _head_idx(0), _tail_idx(0)
{
  if (_databuf == nullptr) {
    _databuf = (T*) malloc(capacity * sizeof(T));
    _is_mem_owner = true;
  }
}


template <typename T>
FixedDeque<T>::~FixedDeque()
{
  if (_is_mem_owner) {
    free(_databuf);
  }
}


template <typename T>
void FixedDeque<T>::push_front(const T& elem)
{
  if (_size == _capacity) {
    return;
  }
  _databuf[_head_idx] = elem;
  _head_idx += 1;
  if (_head_idx == _capacity) {
    _head_idx = 0;
  }
  _size += 1;
}


template <typename T>
void FixedDeque<T>::remove_last()
{
  if (_size == 0) {
    return;
  }
  _tail_idx += 1;
  if (_tail_idx == _capacity) {
    _tail_idx = 0;
  }
  _size -= 1;
}


template <typename T>
const T& FixedDeque<T>::peek_from_tail(unsigned int idx_rel) const
{
  unsigned int index = _tail_idx + idx_rel;
  if (index >= _capacity) {
    index -= _capacity;
  }
  return _databuf[index];
}


template <typename T>
T& FixedDeque<T>::peek_from_tail(unsigned int idx_rel)
{
  return const_cast<T&>(const_cast<const FixedDeque*>(this)->peek_from_tail(idx_rel));
}
