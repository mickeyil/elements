#pragma once

#include <cstddef>
#include <cstdint>

#include "wire_constants.h"

// The device's stable identity. Held by the App; read each tick by the
// link (DEVICE_HELLO write) and the sync client (PING send). Two
// prefix conventions name what kind of device a UID belongs to:
//
//   esp-XXXXXXXXXXXX   real ESP device. The 12 hex chars are the last
//                      six bytes of the chip MAC. Always exactly 16
//                      visible bytes.
//   sim-...........    sim binary process. Suffix is whatever the
//                      developer passed via --device-uid, padded with
//                      \0 to fill the wire slot.
//
// On the wire, the uid occupies a fixed UID_WIRE_SIZE (16) byte slot.
// In memory, uid is a NUL-terminated C string in a UID_CAPACITY (24)
// buffer; that gives strlen / printf room without changing the wire
// shape. Serialization copies min(strlen, UID_WIRE_SIZE) bytes and
// zero-pads the rest of the slot.
//
// boot_token       fresh u32 on every boot. Lets the controller drop
//                  stale device-side state when it sees the bump.
// protocol_version wire generation. Present in DEVICE_HELLO so the
//                  controller can refuse devices it doesn't speak.

constexpr size_t UID_CAPACITY = 24;

struct DeviceIdentity {
    char     uid[UID_CAPACITY] = {};
    uint32_t boot_token        = 0;
    uint8_t  protocol_version  = PROTOCOL_VERSION;
};

// Platform-specific factories live beside platform entry points.
