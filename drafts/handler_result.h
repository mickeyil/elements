#pragma once

#include <cstddef>
#include <cstdint>

namespace controller_link {

// ACK status carried in the outbound 0x80 reply. Wire encoding is one byte.
enum class AckStatus : uint8_t {
    Ok              = 0,
    Error           = 1,
    WrongState      = 2,
    ProfileMismatch = 3,
    BadPayload      = 4,
    UnknownCommand  = 5,
    Unsynced        = 6,
};

// Optional out-of-band signal a handler can ask the parser to honor after
// the ACK is sent. Today only Reboot uses this.
struct ControlSignal {
    bool reboot_requested = false;
};

// What every handler returns to the parser.
struct HandlerResult {
    AckStatus status = AckStatus::Ok;

    // Optional payload to append after the status byte in the ACK frame.
    // The buffer must stay valid until the parser sends the ACK -- handlers
    // typically point at a member buffer.
    const uint8_t* ack_payload = nullptr;
    size_t         ack_payload_len = 0;

    ControlSignal signal{};

    static HandlerResult ok() { return {}; }
    static HandlerResult ok_with_payload(const uint8_t* p, size_t n);
    static HandlerResult error(AckStatus s);
    static HandlerResult reboot();
};

}  // namespace controller_link
