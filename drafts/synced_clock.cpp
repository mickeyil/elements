#include "synced_clock.h"

#ifdef ARDUINO
#include <esp_timer.h>
#else
#include <chrono>
#endif

bool SyncedClock::is_synced() const
{
    return _is_synced;
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

void SyncedClock::apply_sync_offset(int64_t local_minus_remote_us)
{
    _offset_us = local_minus_remote_us;
    _is_synced = true;
}

void SyncedClock::clear_sync()
{
    _offset_us = 0;
    _is_synced = false;
}
