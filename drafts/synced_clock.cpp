#include "synced_clock.h"

bool SyncedClock::is_synced() const
{
    return _is_synced;
}

int64_t SyncedClock::now_synced_us() const
{
    // TODO: final implementation should return disciplined monotonic time
    // derived from the shared raw source plus correction policy.
    return now_unsynced_us() - _offset_us;
}

int64_t SyncedClock::now_unsynced_us() const
{
    // TODO: final implementation should read the raw monotonic source shared
    // by firmware/sim and return it here.
    return 0;
}

void SyncedClock::apply_correction(int64_t offset_us)
{
    _offset_us = offset_us;
    _is_synced = true;
}

void SyncedClock::clear_sync()
{
    _offset_us = 0;
    _is_synced = false;
}
