#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>

#include "core/blob_reader.h"
#include "core/engine.h"
#include "core/hardware_profile.h"
#include "core/program_time.h"
#include "core/strip.h"
#include "controller/synced_clock.h"

// Playback is the synchronous render core. It owns the program lifecycle,
// the engine, the canonical RGB Strip, and a single program-time cursor.
// Owners drive it with command handlers and sample frames via render_*().
//
// The strip changes outside the frame loop too: STOP, LOAD, detach and the
// end of a program all clear it, and MANUAL overwrites it. Every such change
// and every rendered frame raises a presentation request that the owner
// reads with take_present_request() and answers by writing the strip to its
// output, whatever the playback state. That is what keeps the LEDs equal to
// the strip: a STOP blanks them even though no frame is due.
//
// MANUAL holds a constant picture handed over pixel by pixel, with no
// program loaded: nothing renders and the cursor reads 0. Another MANUAL
// replaces the picture, STOP clears it and returns to IDLE, and LOAD, a
// profile change or detach take their usual path; START, RESUME and JUMP
// are WrongState.
//
// Clock domain is selected per loaded program: requires_sync=true reads
// SyncedClock::now_remote_us(); requires_sync=false reads now_local_us().
//
// A looping program never ends: the cursor keeps counting elapsed time,
// and each frame renders at that time modulo the duration. The frame that
// crosses into a new cycle (or skips several) rewinds the engine first.

// New states go last: the controller mirrors this order.
enum class DeviceState : uint8_t { IDLE, LOADED, PLAYING, PAUSED, ENDED, MANUAL };

enum class RenderFrameResult : uint8_t {
    // Strip was not modified by this call.
    Unchanged,

    // Strip contains a newly produced frame.
    Rendered,

    // Natural end-of-program transition. Strip was cleared to black and the
    // state changed PLAYING -> ENDED. Returned only for that transition;
    // later calls return Unchanged. Never returned for a looping program.
    Ended,
};

// Admission result of a playback command, so the command layer can ACK
// the controller truthfully. Rejections leave playback state untouched.
enum class PlaybackResult : uint8_t {
    Ok,
    WrongState,  // command not valid from the current DeviceState
    Unsynced,    // synced program, but the clock lease is not active
    BadTime,     // time argument non-finite, out of range, or behind the cursor
};

class Playback {
public:
    explicit Playback(SyncedClock& clock);
    Playback(uint16_t strip_length, SyncedClock& clock);
    ~Playback();

    bool has_hardware_profile() const;
    const HardwareProfile& hardware_profile() const;

    // Apply a hardware profile. Drops any loaded program, resizes the strip,
    // and returns to IDLE. Returns false on invalid profile or strip-resize
    // failure (in which case no profile is set).
    bool apply_hardware_profile(const HardwareProfile& profile);

    // Decode `blob` and build the engine. Requires a valid hardware profile.
    // Returns false on decode or alloc failure (state stays IDLE).
    //
    // On failure, writes the category to `*err_out` (if non-null):
    //   - decode failures forward `decode_program`'s DecodeError
    //   - engine alloc failure reports `OutOfMemory`
    //   - missing hardware profile leaves `*err_out` untouched (caller bug)
    bool handle_load(const uint8_t* blob, size_t blob_len,
                     DecodeError* err_out = nullptr);

    // Start the program. Valid from LOADED or ENDED. For synced programs,
    // rejected unless SyncedClock::is_synced() is true; the caller's
    // program_start_us is taken as the remote-clock anchor. For unsynced
    // programs, program_start_us is ignored and playback starts immediately
    // (anchor = now_local_us(), cursor = 0).
    PlaybackResult handle_start(int64_t program_start_us);

    // Re-anchor the program-time cursor to t_ms (milliseconds into the
    // program; for a looping program, into the cycle the cursor is in).
    // Valid from LOADED or PAUSED. Target must be strictly ahead of the
    // current cursor and strictly inside [0, duration), else BadTime.
    // Resets the engine, sets cursor to the target, transitions to PAUSED.
    // Does not render.
    PlaybackResult handle_jump(uint32_t t_ms);

    void handle_pause();

    // Resume from PAUSED. For synced programs, rejected unless
    // SyncedClock::is_synced() is true; the caller's program_start_us is
    // taken as the remote-clock anchor. For unsynced programs,
    // program_start_us is ignored and playback resumes immediately from the
    // preserved cursor (anchor = now_local_us() - cursor).
    PlaybackResult handle_resume(int64_t program_start_us);

    // Stop: engine reset, strip cleared. Returns to LOADED when a program is
    // loaded, else (from MANUAL) to IDLE. A no-op from IDLE.
    RenderFrameResult handle_stop();

    // Show a constant picture: `rgb` holds one program-space (pre-gamma)
    // r, g, b triple per pixel. Drops any loaded program and timing, copies
    // the pixels into the strip, enters MANUAL and raises one presentation
    // request, from any state. Returns false, changing nothing, without a
    // hardware profile or unless `len` is exactly strip_length() triples.
    bool handle_manual(const uint8_t* rgb, size_t len);

    // Drop the loaded program and timing; transition to IDLE. The strip is
    // cleared, which raises a presentation request like any other clear.
    void reset_for_detach();

    // Clear the strip (raising a presentation request) and report Rendered.
    RenderFrameResult render_black_frame();

    // Render the next frame from the current state and selected clock.
    // The only path that samples the clock and the engine.
    RenderFrameResult render_next_frame();

    // Does the strip hold output not yet presented? True once after every
    // buffer clear, MANUAL picture and rendered frame; this call consumes
    // it. PAUSE and JUMP leave the strip alone and raise nothing.
    bool take_present_request();

    DeviceState state() const;
    // Program length in ms (one cycle of a looping program); 0 when no
    // program is loaded.
    uint32_t duration_ms() const;
    // Returns 0 when no program is loaded.
    uint8_t target_fps() const;
    // Where the cursor sits: the loop cycle (always 0 for a non-looping
    // program) and ms into it. 0/0 in IDLE, LOADED and MANUAL; ENDED
    // reports the duration.
    uint32_t current_cycle() const;
    uint32_t current_t_ms() const;
    uint16_t strip_length() const;
    bool requires_sync() const;
    bool loop() const;

    Strip& strip();
    const Strip& strip() const;

private:
    int64_t program_clock_now_us() const;
    int64_t duration_us_() const;
    void unload_program_();
    void reset_program_state_();
    void reset_timing_state_();
    void clear_render_buffer_();

    SyncedClock& _clock;
    Strip _strip;
    std::unique_ptr<Engine> _engine;
    HardwareProfile _profile{};

    DeviceState _state = DeviceState::IDLE;
    ProgramDuration _duration;
    uint8_t _target_fps = 0;
    int64_t _program_start_us = 0;
    // Elapsed program time, never wrapped: pause, resume and the rollback
    // guard all work on it. A looping program's position is this modulo
    // the duration.
    int64_t _t_program_cursor_us = 0;
    // The loop cycle the engine's timeline currently belongs to.
    int64_t _engine_cycle = 0;
    bool _requires_sync = false;
    bool _loop = false;
    bool _present_pending = false;
};
