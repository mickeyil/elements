#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

#include "handler_result.h"

class TcpTransport;
class WireReader;
class SessionHandler;
class PlaybackHandler;
class StorageHandler;
class StatusHandler;
class SystemHandler;

// Sits above TcpTransport and below the handlers. Buffers incoming
// bytes, extracts complete length-prefixed messages, dispatches each
// on the high nibble of the opcode, encodes ACKs back to the
// controller, and surfaces control signals (today: reboot) to the
// outer loop.
//
// Platform-agnostic: both ESP and sim use the same instantiation.
// Static wiring -- handlers are passed by reference at construction
// and live at least as long as the parser.
//
// poll() is the single entry point per main-loop tick. The outer loop
// only calls it while transport.is_connected() is true. After the
// connection drops (poll() returns disconnected = true), the outer
// loop disconnects, calls reset_buffer(), and goes back to discovery
// + connect + send_device_hello before calling poll() again.

class CommandParser {
public:
    CommandParser(
        TcpTransport&    transport,
        SessionHandler&  session,
        PlaybackHandler& playback,
        StorageHandler&  storage,
        StatusHandler&   status,
        SystemHandler&   system
    );

    struct PollResult {
        bool disconnected     = false;
        bool reboot_requested = false;
    };

    PollResult poll();

    // Wipe the inbound buffer. Called after a connection drop, and
    // again after a fresh connect succeeds (before send_device_hello).
    void reset_buffer();

    // TODO: malformed-frame policy. Length zero, length over
    // TCP_MSG_MAX, socket EOF -> drop. Well-formed frame with unknown
    // opcode -> ACK UnknownCommand, connection stays up. Encode this
    // discipline in the dispatch path.

    // TODO: backpressure on send. write() failure today drops the
    // client; confirm that's still right under the new transport
    // interface, or distinguish "would block" from "fatal" if the
    // transport API exposes that.

private:
    HandlerResult dispatch_(uint8_t cmd_type, WireReader& reader);

    bool send_ack_(uint8_t status,
                   const uint8_t* payload, size_t payload_len);

    TcpTransport&    _transport;
    SessionHandler&  _session;
    PlaybackHandler& _playback;
    StorageHandler&  _storage;
    StatusHandler&   _status;
    SystemHandler&   _system;

    std::vector<uint8_t> _buf;
    size_t _buf_used = 0;
};
