#include "synced_clock.h"

#ifdef ARDUINO
#include <esp_timer.h>
#else
#include <chrono>
#endif

bool SyncedClock::is_synced() const
{
    return _has_offset && now_local_us() < _valid_until_local_us;
}

int64_t SyncedClock::now_remote_us() const
{
    return now_local_us() - _offset_us;
}

int64_t SyncedClock::now_local_us() const
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

void SyncedClock::apply_sync_offset(int64_t offset_us, int64_t valid_for_us)
{
    _offset_us = offset_us;
    _valid_until_local_us = now_local_us() + valid_for_us;
    _has_offset = true;
}

void SyncedClock::clear_sync()
{
    _offset_us = 0;
    _valid_until_local_us = 0;
    _has_offset = false;
}
