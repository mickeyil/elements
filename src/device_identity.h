#pragma once

#include <cstddef>
#include <cstdint>

#include "link_protocol.h"

// The device's stable identity. Constructed once at boot, then read by
// the controller link (DEVICE_HELLO) and the sync client (PING).
//
// UID prefix conventions:
//   esp-XXXXXXXXXXXX   real ESP. The 12 hex chars are the last six
//                      bytes of the chip MAC.
//   sim-...........    simulated device. "sim-" followed by up to 12
//                      characters.
//
// boot_token lets the controller detect a fresh boot and drop state
// cached for the previous one.

constexpr size_t UID_BUF_SIZE = 24;

struct DeviceIdentity {
    char     uid[UID_BUF_SIZE] = {};
    uint32_t boot_token        = 0;
    uint8_t  protocol_version  = PROTOCOL_VERSION;
};
