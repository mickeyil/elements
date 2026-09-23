#pragma once

#include <cstdint>

// Snapshot the device returns in the QueryDeviceStatus ACK. Owned by
// the App and reachable through AppContext. Populated by whichever
// owner authoritatively knows each field:
//   - mode:           the App (attached / detached / ...).
//   - flags:          bit0 = profile_present, set by the profile loader;
//                     bit1 = clock_synced, mirrored by the App each tick
//                     from SyncedClock::is_synced().
//   - clock_skew_us:  ClockSyncClient::last_skew_us(), mirrored by the
//                     App each tick.
//
// Wire encoding (LE): u8 mode, u8 flags, i32 clock_skew_us. The layout
// is part of the controller-link protocol.

enum class DeviceMode : uint8_t {
    AttachedControlled  = 0,
    DetachedGraceHold   = 1,
    DetachedBlank       = 2,
    DetachedBackground  = 3,
};

// DeviceStatus.flags bit assignments.
constexpr uint8_t STATUS_FLAG_PROFILE_PRESENT = 0x01;
constexpr uint8_t STATUS_FLAG_CLOCK_SYNCED    = 0x02;

struct DeviceStatus
{
    DeviceMode mode          = DeviceMode::DetachedBlank;
    uint8_t    flags         = 0;   // bit0 profile_present, bit1 clock_synced
    int32_t    clock_skew_us = 0;
};
