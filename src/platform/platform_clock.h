#pragma once

#include <cstdint>

// Raw monotonic time source. The exact implementation is selected at link
// time, not compile time: the host build links sim/host_platform_clock.cpp,
// the ARDUINO build links firmware/esp_platform_clock.cpp, and tests link
// a controllable fake (test/test_platform_clock.cpp). SyncedClock and any
// other time-aware module call only this one symbol.

int64_t now_us();
