#pragma once

#include <cstdint>

#include "handler_result.h"

class Playback;

namespace controller_link {

class WireReader;
class SessionHandler;

// Pass-through handler for category 0x1_ commands. Holds no per-command
// state; every command requires session attachment.

class PlaybackHandler {
public:
    PlaybackHandler(Playback& playback, const SessionHandler& session);

    HandlerResult handle(uint8_t opcode, WireReader& r);

    // TODO: Playback::handle_start, handle_resume, and handle_jump are void
    // today (src/playback.cpp). The v3 design requires them to return a
    // status so this handler can ACK truthfully. Track the API change in
    // playback.md and TODO.md; this handler's switch maps the return values
    // onto AckStatus codes (Unsynced, WrongState, BadPayload, Ok).

private:
    HandlerResult handle_load_(WireReader& r);
    HandlerResult handle_start_(WireReader& r);
    HandlerResult handle_jump_(WireReader& r);
    HandlerResult handle_pause_(WireReader& r);
    HandlerResult handle_resume_(WireReader& r);
    HandlerResult handle_stop_(WireReader& r);

    Playback&             _playback;
    const SessionHandler& _session;
};

}  // namespace controller_link
