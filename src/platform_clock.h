#pragma once

#include <cstdint>

// Raw monotonic time source. The exact implementation is selected at link
// time, not compile time: the host build links platform_clock_host.cpp,
// the ARDUINO build links platform_clock_esp.cpp, and tests link a
// controllable fake (test/platform_clock_test.cpp). SyncedClock and any
// other time-aware module call only this one symbol.

int64_t now_us();
