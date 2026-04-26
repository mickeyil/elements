#include "synced_clock.h"

#include <cassert>

#include "platform_clock.h"

bool SyncedClock::is_synced() const
{
    return _has_offset && platform_clock::now_us() < _valid_until_local_us;
}

int64_t SyncedClock::now_remote_us() const
{
    return platform_clock::now_us() - _offset_us;
}

int64_t SyncedClock::now_local_us() const
{
    return platform_clock::now_us();
}

void SyncedClock::apply_sync_offset(int64_t offset_us, int64_t valid_for_us)
{
    assert(valid_for_us >= 0);

    _offset_us = offset_us;
    _valid_until_local_us = platform_clock::now_us() + valid_for_us;
    _has_offset = true;
}

void SyncedClock::clear_sync()
{
    _offset_us = 0;
    _valid_until_local_us = 0;
    _has_offset = false;
}
