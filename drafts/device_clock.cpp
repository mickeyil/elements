#include "device_clock.h"

bool DeviceClock::is_synced() const
{
    return _is_synced;
}

int64_t DeviceClock::now_controller_us() const
{
    // TODO: final implementation should return disciplined monotonic time
    // derived from the shared raw source plus correction policy.
    return now_local_us() - _offset_us;
}

int64_t DeviceClock::now_local_us() const
{
    // TODO: final implementation should read the raw monotonic source shared
    // by firmware/sim and return it here.
    return 0;
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
