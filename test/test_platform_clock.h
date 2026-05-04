#pragma once

#include <cstdint>

// Test-only control surface for the platform clock fake. Only the test
// translation unit (test/test_platform_clock.cpp) defines these alongside
// now_us(); production builds neither include this header
// nor link the fake.

void set_test_now_us(int64_t now_us);
void advance_test_us(int64_t delta_us);
