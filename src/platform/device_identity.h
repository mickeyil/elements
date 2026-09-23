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
//
// version is the build's version string, reported in DISCOVER so the
// controller can show what a device runs (and what an update would
// replace) before, or without, a link: a device the controller refuses
// still broadcasts. Firmware: "<major>.<minor>" with "+d" for a dirty
// tree (tools/firmware_version.py). Sim: short commit hash, same "+d".
// Empty when the build had no version.

// Fixed-size UID slot on the wire. ASCII, null-padded if shorter.
constexpr size_t UID_SIZE = 16;

// In-memory UID buffer. Larger than the wire slot so callers can keep
// a null-terminated C string and still copy UID_SIZE bytes to the wire.
constexpr size_t UID_BUF_SIZE = 24;

// Fixed-size version slot on the wire. ASCII, null-padded if shorter;
// a longer version is truncated there, never rejected.
constexpr size_t VERSION_SIZE = 16;

// In-memory version buffer; same headroom rule as UID_BUF_SIZE.
constexpr size_t VERSION_BUF_SIZE = 24;

struct DeviceIdentity {
    char     uid[UID_BUF_SIZE]         = {};
    uint32_t boot_token                = 0;
    char     version[VERSION_BUF_SIZE] = {};
};
