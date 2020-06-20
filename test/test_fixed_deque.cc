#include <cassert>
#include <cstdio>

#include "src/fixed_deque.h"

static int dqbuf[4] = { 0 };

int main()
{
  FixedDeque<int> dq(4, dqbuf);
  assert(dq.capacity() == 4);
  assert(dq.size() == 0);
  dq.push_front(10);
  assert(dq.size() == 1);
  assert(dq.peek_from_tail(0) == 10);
  dq.push_front(11);
  dq.push_front(12);
  dq.push_front(13);
  assert(dq.size() == 4);
  dq.push_front(999);
  assert(dq.peek_from_tail(0) == 10 && dq.peek_from_tail(3) == 13);
  dq.remove_last();
  assert(dq.peek_from_tail(0) == 11);
  dq.push_front(14);
  assert(dq.peek_from_tail(3) == 14);
  return 0;
}