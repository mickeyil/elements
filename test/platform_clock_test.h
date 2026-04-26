#pragma once

#include <cstdint>

// Test-only control surface for the platform_clock fake. Only the test
// translation unit (test/platform_clock_test.cpp) defines these alongside
// platform_clock::now_us(); production builds neither include this header
// nor link the fake.

namespace platform_clock {

void set_test_now_us(int64_t now_us);
void advance_test_us(int64_t delta_us);

}  // namespace platform_clock
