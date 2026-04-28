#pragma once

#include <cstddef>
#include <cstdint>

#include "wire_constants.h"

// DEVICE_HELLO is the first TCP message after connect. It carries the
// device's identity and the OFFER nonce; the controller validates and
// either starts sending commands (success) or silently closes the
// connection (failure). There is deliberately no separate ACK -- the
// device infers acceptance from the controller's continued presence,
// rejection from the next read returning < 0.

namespace controller_link {

class TcpTransport;

// Device identity. UID is a fixed 16-byte ASCII slot, null-padded if
// shorter. Two prefix conventions tell the controller (and the human
// reading logs) what kind of device this is at a glance:
//   - "esp-XXXXXXXXXXXX" -- real ESP device. The 12 hex chars are the
//                           last six bytes of the chip MAC.
//   - "sim-..........." -- sim binary. Suffix is whatever the developer
//                          passes via --device-uid.
//
// boot_token       : fresh 32-bit random value generated on every boot
//                    (and on simulated reboot). Lets the controller
//                    detect "device rebooted, drop stale state."
// protocol_version : wire protocol generation. Defaulted to current.

struct DeviceIdentity {
    char     uid[UID_SIZE] = {};   // null-padded ASCII
    uint32_t boot_token = 0;
    uint8_t  protocol_version = PROTOCOL_VERSION;
};

// Build and send DEVICE_HELLO over the connected transport.
//
// Called once, immediately after TcpTransport::connect() returns true,
// before CommandParser::poll() starts. offer_nonce comes from the
// OFFER packet that preceded this connect.
//
// Returns false if the socket write fails -- the caller should
// disconnect and back off. Success here means "bytes left the device,"
// not "controller accepted." Acceptance is implicit: the next bytes
// the device receives will be a normal command frame.
bool send_device_hello(TcpTransport& transport,
                       const DeviceIdentity& identity,
                       uint32_t offer_nonce);

}  // namespace controller_link
