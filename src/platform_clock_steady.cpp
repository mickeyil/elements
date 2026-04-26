#include "platform_clock.h"

#include <chrono>

int64_t platform_clock::now_us()
{
    using namespace std::chrono;
    return duration_cast<microseconds>(
               steady_clock::now().time_since_epoch())
        .count();
}
