#pragma once

#include <cstdint>

#include "../src/device_identity.h"

// DEVICE_HELLO is the first TCP message after connect. It carries the
// device's identity and the OFFER nonce; the controller validates and
// either starts sending commands (success) or silently closes the
// connection (failure). There is deliberately no separate ACK; the
// device infers acceptance from the controller's continued presence,
// rejection from the next read returning < 0.
//
// The canonical DeviceIdentity struct lives in src/device_identity.h
// (no namespace, top level). This header just adds the wire helper.

class TcpTransport;

// Build and send DEVICE_HELLO over the connected transport.
//
// Called once, immediately after TcpTransport::connect() returns true,
// before CommandParser::poll() starts. offer_nonce comes from the
// OFFER packet that preceded this connect.
//
// Returns false if the socket write fails; the caller should
// disconnect and back off. Success here means "bytes left the device,"
// not "controller accepted." Acceptance is implicit: the next bytes
// the device receives will be a normal command frame.
bool send_device_hello(TcpTransport& transport,
                       const DeviceIdentity& identity,
                       uint32_t offer_nonce);
