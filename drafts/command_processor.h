#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

class TcpTransport;
class CommandHandler;

// Owns the command-stream loop: read bytes from TcpTransport, parse one
// length-prefixed frame, call CommandHandler::handle(), send the ACK.
// Knows no opcodes; everything protocol-meaningful lives in
// CommandHandler.
//
// Platform-agnostic: ESP and sim use the same instantiation. Lives at
// least as long as the TcpTransport it borrows.
//
// poll() processes at most one complete frame per call and returns true
// iff a frame was handled. The owning ControllerLink uses that return
// as liveness evidence: any handled frame counts (Ping has no special
// path), so a successful poll() bumps the link's _last_activity_us.
//
// If two frames are buffered, the second waits until the next loop
// iteration. If the first was Reboot, the second never runs because
// the App reboots between ticks. That is intentional.
//
// Failure handling:
//   - length == 0 or length > TCP_MSG_MAX  : drop the connection.
//   - incomplete frame                     : keep buffering, return false.
//   - unknown opcode                       : ACK UnknownCommand, link stays up.
//   - ACK write fails                      : best-effort, no special path.
//
// reset_buffer() is called by ControllerLink after a connection drop
// and again after a fresh connect, before the REGISTER write.

constexpr size_t REPLY_PAYLOAD_MAX = 1024;

class CommandProcessor {
public:
    CommandProcessor(TcpTransport& transport, CommandHandler& handler);

    bool poll();

    void reset_buffer();

private:
    bool send_ack_(uint8_t status, const uint8_t* payload, size_t payload_len);

    TcpTransport&   _transport;
    CommandHandler& _handler;

    std::vector<uint8_t> _rx;
    size_t               _rx_used = 0;

    uint8_t _reply_payload[REPLY_PAYLOAD_MAX] = {};
};
