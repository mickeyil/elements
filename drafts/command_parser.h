#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

#include "handler_result.h"

class TcpTransport;
class CommandHandler;

// Sits above TcpTransport and below CommandHandler. Buffers incoming
// bytes, extracts complete length-prefixed messages, hands each
// opcode + payload to CommandHandler, encodes ACKs back to the
// controller, and surfaces control signals (today: reboot) to the
// outer loop.
//
// The parser knows no opcodes: it does framing and ACK encoding only.
// All dispatch and protocol semantics live in CommandHandler.
//
// Platform-agnostic: both ESP and sim use the same instantiation.
// Static wiring -- the handler is passed by reference at construction
// and lives at least as long as the parser.
//
// poll() is the single entry point per main-loop tick. The outer loop
// only calls it while transport.is_connected() is true. After the
// connection drops (poll() returns disconnected = true), the outer
// loop disconnects, calls reset_buffer(), and goes back to discovery
// + connect + send_identity before calling poll() again.

class CommandParser {
public:
    CommandParser(TcpTransport& transport, CommandHandler& handler);

    struct PollResult {
        bool disconnected     = false;
        bool reboot_requested = false;
    };

    PollResult poll();

    // Wipe the inbound buffer. Called after a connection drop, and
    // again after a fresh connect succeeds (before send_identity).
    void reset_buffer();

    // TODO: malformed-frame policy. Length zero, length over
    // TCP_MSG_MAX, socket EOF -> drop. Well-formed frame with unknown
    // opcode -> ACK UnknownCommand, connection stays up. Encode this
    // discipline in the frame-handling path.

    // TODO: backpressure on send. write() failure today drops the
    // client; confirm that's still right under the new transport
    // interface, or distinguish "would block" from "fatal" if the
    // transport API exposes that.

private:
    bool send_ack_(uint8_t status,
                   const uint8_t* payload, size_t payload_len);

    TcpTransport&   _transport;
    CommandHandler& _handler;

    std::vector<uint8_t> _buf;
    size_t _buf_used = 0;
};
