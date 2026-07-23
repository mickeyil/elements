#pragma once

#include <cstddef>
#include <cstdint>

#include "app/link_protocol.h"

class TcpTransport;
class CommandHandler;

// Outcome of one CommandProcessor::poll() call.
enum class PollResult : uint8_t {
    Idle,     // no complete message this tick, or still draining an ACK
    Handled,  // one message parsed, handled, and its ACK staged/sent
    Fault,    // unrecoverable stream state; the owner must drop the connection
};

// Reply payload scratch size. Sized in command_processor.cpp against the
// largest reply (QueryLocalAnimations at full store capacity).
constexpr size_t REPLY_PAYLOAD_MAX = 1024;

// Largest outbound frame: length u32 + opcode + status + reply payload.
constexpr size_t TX_FRAME_MAX = 4 + 2 + REPLY_PAYLOAD_MAX;

// Runs the command stream above a connected TcpTransport: reads bytes,
// parses one length-prefixed message per poll(), hands it to
// CommandHandler, and sends the ACK back. Knows no opcodes; everything
// protocol-meaningful lives in the handler.
//
// Writes never block. An ACK the socket will not take whole stays in
// _tx as a pending tail, and no new command is parsed until the tail
// has drained; at most one outbound frame ever exists. Inbound bytes
// keep being read meanwhile so peer close is noticed, but a stalled
// socket stalls handling, which lets the owner's liveness deadline
// (no Handled for PING_TIMEOUT_MS) double as the TX stall deadline.
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
// Faults are zero or oversized message lengths and transport read or
// write errors (including peer close). An unknown opcode is not a
// fault: it ACKs UnknownCommand and the stream continues.
//
// _rx is a fixed TCP_MSG_MAX (~16 KiB) array, allocated once with the
// processor, so the hot path where blobs and decoder allocations
// interleave never touches the heap.

class CommandProcessor
{
public:
    CommandProcessor(TcpTransport& transport, CommandHandler& handler);

    PollResult poll();

    // Drop all stream state, RX bytes and the pending ACK tail both.
    // The owner calls this around connection changes so a new stream
    // never resumes mid-message and never replays bytes framed for a
    // dead connection.
    void reset_stream();

    // Is an ACK tail still waiting for socket buffer space?
    bool tx_pending() const { return _tx_sent < _tx_len; }

    // Push pending ACK bytes as far as the socket allows right now.
    // Returns false on transport error.
    bool flush_tx();

private:
    void stage_ack_(uint8_t status, const uint8_t* payload, size_t payload_len);

    TcpTransport&   _transport;
    CommandHandler& _handler;

    uint8_t _rx[TCP_MSG_MAX] = {};
    size_t  _rx_used = 0;   // bytes in [0, _rx_used) are valid

    uint8_t _reply_payload[REPLY_PAYLOAD_MAX] = {};

    // The one in-flight outbound frame: bytes in [_tx_sent, _tx_len)
    // still need the socket.
    uint8_t _tx[TX_FRAME_MAX] = {};
    size_t  _tx_len  = 0;
    size_t  _tx_sent = 0;
};
