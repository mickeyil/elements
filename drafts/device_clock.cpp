#include "device_clock.h"

#ifdef ARDUINO
#include <esp_timer.h>
#else
#include <chrono>
#endif

bool DeviceClock::is_synced() const
{
    return _is_synced;
}

int64_t DeviceClock::now_controller_us() const
{
    return now_local_us() - _offset_us;
}

int64_t DeviceClock::now_local_us() const
{
#ifdef ARDUINO
    return esp_timer_get_time();
#else
    using namespace std::chrono;
    return duration_cast<microseconds>(
               steady_clock::now().time_since_epoch())
        .count();
#endif
}

void DeviceClock::apply_sync_offset(int64_t local_minus_controller_us)
{
    _offset_us = local_minus_controller_us;
    _is_synced = true;
}

void DeviceClock::clear_sync()
{
    _offset_us = 0;
    _is_synced = false;
}
