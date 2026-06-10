#include "command_processor.h"

#include <cstring>

#include "animation_store.h"
#include "command_handler.h"
#include "tcp_transport.h"
#include "wire_writer.h"

// Largest reply payload: QueryLocalAnimations at full store capacity.
static_assert(REPLY_PAYLOAD_MAX >=
                  2 + MAX_STORED_ANIMATIONS * (ANIM_NAME_SIZE + 2 + 4),
              "reply buffer too small for QueryLocalAnimations");

namespace {

// Inbound message: length u32 LE | opcode u8 | payload. The length
// counts opcode + payload; a whole valid message fits the RX buffer.
constexpr size_t MSG_HEADER_BYTES = 4;
constexpr size_t MAX_MSG_LENGTH = TCP_MSG_MAX - MSG_HEADER_BYTES;

uint32_t read_u32_le(const uint8_t* p)
{
    return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) |
           (uint32_t(p[3]) << 24);
}

}  // namespace

CommandProcessor::CommandProcessor(TcpTransport& transport,
                                   CommandHandler& handler)
    : _transport(transport), _handler(handler)
{
}

PollResult CommandProcessor::poll()
{
    if (_rx_used < sizeof(_rx)) {
        const int n = _transport.read(_rx + _rx_used, sizeof(_rx) - _rx_used);
        if (n < 0) return PollResult::Fault;
        _rx_used += static_cast<size_t>(n);
    }

    if (_rx_used < MSG_HEADER_BYTES) return PollResult::Idle;

    const uint32_t length = read_u32_le(_rx);
    if (length == 0 || length > MAX_MSG_LENGTH) return PollResult::Fault;

    const size_t msg_bytes = MSG_HEADER_BYTES + length;
    if (_rx_used < msg_bytes) return PollResult::Idle;

    const uint8_t opcode = _rx[MSG_HEADER_BYTES];
    const uint8_t* payload = _rx + MSG_HEADER_BYTES + 1;
    const size_t payload_len = length - 1;

    WireWriter reply(_reply_payload, sizeof(_reply_payload));
    const AckStatus status = _handler.handle(opcode, payload, payload_len, reply);
    send_ack_(static_cast<uint8_t>(status), _reply_payload,
              reply.bytes_written());

    std::memmove(_rx, _rx + msg_bytes, _rx_used - msg_bytes);
    _rx_used -= msg_bytes;
    return PollResult::Handled;
}

void CommandProcessor::reset_buffer()
{
    _rx_used = 0;
}

bool CommandProcessor::send_ack_(uint8_t status, const uint8_t* payload,
                                 size_t payload_len)
{
    // Outbound ACK: length u32 LE | 0x80 | status | reply payload.
    uint8_t msg[MSG_HEADER_BYTES + 2 + REPLY_PAYLOAD_MAX];
    const uint32_t length = static_cast<uint32_t>(2 + payload_len);
    msg[0] = length & 0xFF;
    msg[1] = (length >> 8) & 0xFF;
    msg[2] = (length >> 16) & 0xFF;
    msg[3] = (length >> 24) & 0xFF;
    msg[4] = CMD_ACK;
    msg[5] = status;
    std::memcpy(msg + 6, payload, payload_len);
    return _transport.write(msg, MSG_HEADER_BYTES + length);
}
