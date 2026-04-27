#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

#include "handler_result.h"

namespace controller_link {

class TcpTransport;
class WireReader;
class SessionHandler;
class PlaybackHandler;
class StorageHandler;
class StatusHandler;
class SystemHandler;

// CommandParser sits between TcpTransport and the five category handlers.
// One module: byte buffering, length-frame extraction, nibble dispatch,
// ACK encoding. Static wiring -- handlers are passed by reference at
// construction and live at least as long as the parser.

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

    // Drain bytes from the transport, parse complete messages, dispatch
    // each, send ACKs. One call per main-loop tick.
    PollResult poll();

    // Wipe the inbound buffer. Called on accept and on disconnect.
    void reset_buffer();

    // TODO: malformed-frame policy. Length over TCP_MSG_MAX, or any byte
    // arriving that can never form a valid frame, drops the connection.
    // Well-formed frame with unknown opcode -> AckStatus::UnknownCommand,
    // connection stays up. Encode this discipline in the dispatch code.

    // TODO: backpressure on send. write() failure today drops the client;
    // confirm that's still right under the new transport interface, or
    // distinguish "would block" from "fatal" if the transport API exposes
    // that.

private:
    HandlerResult dispatch_(uint8_t cmd_type, WireReader& reader);

    bool send_ack_(AckStatus status,
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

}  // namespace controller_link
