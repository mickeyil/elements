#pragma once

#include "../src/device_identity.h"

// REGISTER is the first TCP message after connect. It identifies the
// device to the controller; the controller validates and either
// starts sending commands (success) or silently closes the
// connection (failure). There is deliberately no separate ACK; the
// device infers acceptance from the controller's continued presence,
// rejection from the next read returning < 0.

class TcpTransport;

// Build and send REGISTER over the connected transport.
//
// Called once, immediately after TcpTransport::connect() returns
// true, before CommandParser::poll() starts.
//
// Returns false if the socket write fails; the caller should
// disconnect and back off. Success here means "bytes left the
// device," not "controller accepted." Acceptance is implicit: the
// next bytes the device receives will be a normal command frame.
bool send_register(TcpTransport& transport, const DeviceIdentity& identity);
