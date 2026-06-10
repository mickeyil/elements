#pragma once

#include <cstddef>
#include <cstdint>

#include "link_protocol.h"

class TcpTransport;
class CommandHandler;

// Outcome of one CommandProcessor::poll() call.
enum class PollResult : uint8_t {
    Idle,     // no complete message this tick
    Handled,  // one message parsed, handled, and ACKed
    Fault,    // unrecoverable stream state; the owner must drop the connection
};

// Reply payload scratch size. Sized in command_processor.cpp against the
// largest reply (QueryLocalAnimations at full store capacity).
constexpr size_t REPLY_PAYLOAD_MAX = 1024;

// Runs the command stream above a connected TcpTransport: reads bytes,
// parses one length-prefixed message per poll(), hands it to
// CommandHandler, and writes the ACK back. Knows no opcodes; everything
// protocol-meaningful lives in the handler.
//
// The owning ControllerLink polls this only while connected, treats
// Handled as controller-liveness evidence, and owns all teardown: on
// Fault it disconnects and resets state. The processor never
// disconnects the transport itself.
//
// One message per poll: a second buffered message waits for the next
// tick. If the first was Reboot, the second never runs because the App
// reboots between ticks. That is intentional.
//
// Faults are zero or oversized message lengths and transport read
// errors (including peer close). An unknown opcode is not a fault: it
// ACKs UnknownCommand and the stream continues. A failed ACK write is
// best-effort; the write marks the transport disconnected and the next
// poll() faults.
//
// _rx is a fixed TCP_MSG_MAX (~16 KiB) array, allocated once with the
// processor, so the hot path where blobs and decoder allocations
// interleave never touches the heap.

class CommandProcessor {
public:
    CommandProcessor(TcpTransport& transport, CommandHandler& handler);

    PollResult poll();

    // Drop any buffered bytes. The owner calls this around connection
    // changes so a new stream never resumes mid-message.
    void reset_buffer();

private:
    bool send_ack_(uint8_t status, const uint8_t* payload, size_t payload_len);

    TcpTransport&   _transport;
    CommandHandler& _handler;

    uint8_t _rx[TCP_MSG_MAX] = {};
    size_t  _rx_used = 0;   // bytes in [0, _rx_used) are valid

    uint8_t _reply_payload[REPLY_PAYLOAD_MAX] = {};
};
