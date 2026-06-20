#pragma once

#include <cstdint>

// Snapshot the device returns in the QueryDeviceStatus ACK. Owned by
// the App and reachable through AppContext. Populated by whichever
// owner authoritatively knows each field:
//   - mode:             FirmwareApp / SimApp (attached / detached / ...).
//   - flags:            bit0 = profile_present, set by the profile loader.
//   - animation_count:  AnimationStore::count() at the time of the query.
//                       Filled by the handler at reply time rather than
//                       cached, since it changes on every Store / Erase.
//
// Wire encoding (LE): u8 mode, u8 flags, u16 animation_count. The
// layout is part of the controller-link protocol.

enum class DeviceMode : uint8_t {
    AttachedControlled  = 0,
    DetachedGraceHold   = 1,
    DetachedBlank       = 2,
    DetachedBackground  = 3,
};

// DeviceStatus.flags bit assignments.
constexpr uint8_t STATUS_FLAG_PROFILE_PRESENT = 0x01;

struct DeviceStatus
{
    DeviceMode mode  = DeviceMode::DetachedBlank;
    uint8_t    flags = 0;   // bit0 profile_present
};
