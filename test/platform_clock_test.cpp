#include "platform_clock.h"
#include "platform_clock_test.h"

namespace platform_clock {

namespace {
int64_t g_now_us = 0;
}  // namespace

int64_t now_us()
{
    return g_now_us;
}

void set_test_now_us(int64_t now_us)
{
    g_now_us = now_us;
}

void advance_test_us(int64_t delta_us)
{
    g_now_us += delta_us;
}

}  // namespace platform_clock
