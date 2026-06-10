#pragma once

#include <cstddef>
#include <cstdint>

struct AppContext;
class WireReader;
class WireWriter;

// Result of one inbound command, sent to the controller as the status
// byte of the ACK reply. Wire values are mirrored in src/link_protocol.h.
enum class AckStatus : uint8_t {
    Ok              = 0,
    Error           = 1,  // device-side failure: storage, allocation, bad blob
    WrongState      = 2,  // command not valid in the current playback state
    ProfileMismatch = 3,  // blob built for a different strip length
    BadPayload      = 4,  // malformed payload: too short, trailing bytes, bad values
    UnknownCommand  = 5,  // unrecognized opcode; the link stays up
    Unsynced        = 6,  // synced program rejected: no active clock lease
};

// Executes inbound controller commands. CommandProcessor frames a
// message off the TCP stream and calls handle(); the opcode switch
// picks a handle_* method that parses the payload, calls the right
// AppContext object (Playback, AnimationStore, ...), and returns the
// AckStatus for the reply. Handlers are translators only; the real
// behavior lives in the AppContext objects. Opcode constants and
// payload layouts: src/link_protocol.h and drafts/controller_link.md.
//
// A payload with unconsumed trailing bytes ACKs BadPayload. Reply
// payloads (the two query commands) go through the caller's
// WireWriter; the processor reads the length off bytes_written().
//
// Two commands act beyond their ACK, through AppContext flags:
//   - Reboot sets reboot_requested; the App reboots after the ACK is
//     flushed. SystemPlatform is never called from here.
//   - PlayLocalAnimation sets local_program_loaded, which makes the
//     App's render loop restart the animation on Ended.
//
// Inbound REGISTER (0x00) is not handled; it is the link's one-shot
// outbound hello and ACKs UnknownCommand like any other unknown opcode.

class CommandHandler {
public:
    explicit CommandHandler(AppContext& ctx);

    // Dispatch one framed command. `reply` writes into the processor's
    // reply buffer; the reply length is reply.bytes_written() afterward.
    AckStatus handle(uint8_t opcode,
                     const uint8_t* payload, size_t payload_len,
                     WireWriter& reply);

private:
    AckStatus handle_set_profile_(WireReader& r);
    AckStatus handle_load_(WireReader& r);
    AckStatus handle_start_(WireReader& r);
    AckStatus handle_jump_(WireReader& r);
    AckStatus handle_pause_(WireReader& r);
    AckStatus handle_resume_(WireReader& r);
    AckStatus handle_stop_(WireReader& r);
    AckStatus handle_play_local_animation_(WireReader& r);
    AckStatus handle_store_animation_(WireReader& r);
    AckStatus handle_erase_animation_(WireReader& r);
    AckStatus handle_set_animation_order_(WireReader& r);
    AckStatus handle_reboot_(WireReader& r);
    AckStatus handle_query_device_status_(WireReader& r, WireWriter& w);
    AckStatus handle_ping_(WireReader& r);
    AckStatus handle_query_local_animations_(WireReader& r, WireWriter& w);

    AppContext& _ctx;
};
