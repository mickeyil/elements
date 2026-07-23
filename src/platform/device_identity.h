#pragma once

#include <cstddef>
#include <cstdint>

// The device's stable identity. Constructed once at boot, then read by
// discovery, the controller link, and the sync client.
//
// UID prefix conventions:
//   esp-XXXXXXXXXXXX   real ESP. The 12 hex chars are the last six
//                      bytes of the chip MAC.
//   sim-...........    simulated device. "sim-" followed by up to 12
//                      characters.
//
// boot_token lets the controller detect a fresh boot and drop state
// cached for the previous one.

// Fixed-size UID slot on the wire. ASCII, null-padded if shorter.
constexpr size_t UID_SIZE = 16;

// In-memory UID buffer. Larger than the wire slot so callers can keep
// a null-terminated C string and still copy UID_SIZE bytes to the wire.
constexpr size_t UID_BUF_SIZE = 24;

struct DeviceIdentity {
    char     uid[UID_BUF_SIZE] = {};
    uint32_t boot_token        = 0;
};
