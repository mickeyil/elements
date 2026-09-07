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
// Clock domain is selected per loaded program: requires_sync=true reads
// SyncedClock::now_remote_us(); requires_sync=false reads now_local_us().

enum class DeviceState : uint8_t { IDLE, LOADED, PLAYING, PAUSED, ENDED };

enum class RenderFrameResult : uint8_t {
    // Strip was not modified by this call.
    Unchanged,

    // Strip contains a newly produced frame.
    Rendered,

    // Natural end-of-program transition. Strip was cleared to black and the
    // state changed PLAYING -> ENDED. Returned only for that transition;
    // later calls return Unchanged.
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

    // Re-anchor the program-time cursor to t_program (seconds, rounded to
    // the nearest millisecond). Valid from LOADED or PAUSED. Target must be
    // strictly ahead of the current cursor and strictly inside
    // [0, duration), else BadTime. Resets the engine, sets cursor to the
    // target, transitions to PAUSED. Does not render.
    PlaybackResult handle_jump(float t_program);

    void handle_pause();

    // Resume from PAUSED. For synced programs, rejected unless
    // SyncedClock::is_synced() is true; the caller's program_start_us is
    // taken as the remote-clock anchor. For unsynced programs,
    // program_start_us is ignored and playback resumes immediately from the
    // preserved cursor (anchor = now_local_us() - cursor).
    PlaybackResult handle_resume(int64_t program_start_us);

    // Stop and return to LOADED. Engine reset, strip cleared.
    RenderFrameResult handle_stop();

    // Drop the loaded program and timing; transition to IDLE. Strip is
    // cleared but no presentation result is returned.
    void reset_for_detach();

    // Clear the strip and report Rendered so the owner can present black.
    RenderFrameResult render_black_frame();

    // Render the next frame from the current state and selected clock.
    // The only path that samples the clock and the engine.
    RenderFrameResult render_next_frame();

    DeviceState state() const;
    // Program length in seconds; 0 when no program is loaded.
    float duration() const;
    // Returns 0 when no program is loaded.
    uint8_t target_fps() const;
    float current_t_program() const;
    uint16_t strip_length() const;
    bool requires_sync() const;

    Strip& strip();
    const Strip& strip() const;

private:
    int64_t program_clock_now_us() const;
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
    int64_t _t_program_cursor_us = 0;
    bool _requires_sync = false;
};
