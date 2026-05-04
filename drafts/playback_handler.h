#pragma once

#include <cstdint>

#include "handler_result.h"

class Playback;

class WireReader;

// Pass-through for category 0x1_ commands (Load, Start, Jump, Pause,
// Resume, Stop) into Playback. Holds no per-command state. The ACK
// status maps directly from Playback's return values -- specifically
// from Unsynced (rejected because clock isn't leased), WrongState
// (rejected because of current playback state), BadPayload
// (non-finite floats, etc.), or Ok.

class PlaybackHandler {
public:
    explicit PlaybackHandler(Playback& playback);

    HandlerResult handle(uint8_t opcode, WireReader& r);

    // TODO: Playback::handle_start / handle_resume / handle_jump are
    // void today (src/playback.cpp). The v3 design needs them to return
    // status so this handler can ACK truthfully -- the parser layer
    // can't distinguish "rejected because unsynced" from "no-op because
    // wrong state" without that. Tracked in drafts/TODO.md § Playback.

private:
    HandlerResult handle_load_(WireReader& r);
    HandlerResult handle_start_(WireReader& r);
    HandlerResult handle_jump_(WireReader& r);
    HandlerResult handle_pause_(WireReader& r);
    HandlerResult handle_resume_(WireReader& r);
    HandlerResult handle_stop_(WireReader& r);

    Playback& _playback;
};
