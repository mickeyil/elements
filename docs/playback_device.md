# PlaybackDevice — Shared Device Abstraction

> **Status: Implemented.** Base class in `src/playback_device.h` / `src/playback_device.cpp`. Tests in `test/test_playback_device.cpp`. `ESPSimulated` implemented as in-process desktop simulator (`src/esp_simulated.h/cpp`, tests in `test/test_esp_simulated.cpp`). `ESPDevice` (real hardware) is not yet implemented.
>
> Related docs: `transport.md` (device protocol, clock sync), `controller.md` (controller/web-app architecture, identity model, reset-safe intervals).

## Overview

Both runtime targets (real ESP32 and desktop simulator) share the same playback logic: blob loading, Engine/Strip lifecycle, per-frame tick, and state management. The base class `PlaybackDevice` captures this shared behavior. Current class hierarchy: `PlaybackDevice` (abstract base) → `ESPSimulated` (implemented, in-process desktop simulator) + `ESPDevice` (planned, real hardware). The transport wrapper `network_sim` provides TCP command input and UDP frame output for `ESPSimulated`.

---

## State machine

```
       LOAD              START             tick returns false
  IDLE -----> LOADED --------> PLAYING ----------> ENDED
    ^          ^ ^              |   ^               |
    |          | | STOP         |   | RESUME        | LOAD
    |          | |--------------|   |               |
    |          | |              |   |-------+       |
    |          | |         PAUSE|           |       |
    |          | |              v           |       |
    |          | |           PAUSED --------+       |
    |          | |              |  |                |
    |          | +----- STOP ---+  |                |
    |          |                   |                |
    |          | LOAD              | LOAD           |
    +----------+-------------------+----------------+
```

A new LOAD at any point tears down the current program and replaces it. PAUSE and RESUME are production commands available on all devices — they preserve engine state and coordinate with audio. STOP clears output to black, resets to t=0, and transitions to LOADED — the program remains loaded and ready to play again.

JUMP is valid in PLAYING, PAUSED, LOADED, and ENDED states. It resets the engine and adjusts the time origin so playback continues (or pauses) at the target time.

---

## Class outline

```cpp
class PlaybackDevice {
public:
    PlaybackDevice(uint16_t strip_length, bool gamma_enabled = true);
    virtual ~PlaybackDevice();

    // --- Command handlers (called by subclass when a command arrives) ---
    bool handle_load(const uint8_t* blob, size_t blob_len, uint16_t gen);
    void handle_start(int64_t t0);
    void handle_jump(int64_t t0, float t_rel, uint16_t gen);
    void handle_pause();
    void handle_resume(int64_t t0);
    void handle_stop();
    void handle_sync_result(int64_t offset);

    // --- Per-iteration logic (called from platform loop) ---
    // Returns true if still active (LOADED, PLAYING, or PAUSED).
    // Returns false if IDLE or ENDED.
    bool tick_once();

    // --- State queries ---
    DeviceState state() const;
    float current_t_rel() const;
    float duration() const;
    const uint8_t* rgb_data() const;
    uint8_t* rgb_buf();
    uint16_t strip_length() const;

protected:
    // --- Platform-specific (virtual, implemented by subclasses) ---
    virtual int64_t now_mono() const = 0;     // monotonic clock, microseconds
    virtual void output_frame(float t_rel) = 0; // push rgb buffer to output
    virtual void send_telemetry(DeviceState s, float t,
                                const char* err = nullptr) {}  // default no-op

    // --- Internals ---
    uint16_t _strip_length;
    uint8_t* _rgb_buf;             // owned, allocated in constructor
    Strip* _strip;                  // view over _rgb_buf
    Engine* _engine = nullptr;      // owns Program*, created on handle_load
    DeviceState _state = DeviceState::IDLE;
    float _duration = 0.0f;
    int64_t _t0 = 0;               // absolute start time (us)
    int64_t _sync_offset = 0;      // controller-provided clock offset (us)
    uint16_t _gen = 0;             // generation counter, echoed on outbound UDP
    uint32_t _frame_index = 0;     // monotonic frame counter, reset on LOAD/START/STOP/JUMP
    float _paused_t_rel = 0.0f;    // t_rel at pause time
    bool _gamma_enabled;
};
```

---

## Key method behavior

### handle_load()

Tears down any existing program, decodes the new blob, creates Engine. On decode failure, clears to black, transitions to IDLE, and reports via telemetry. On success, sets state to LOADED and resets `_gen`, `_frame_index`, `_paused_t_rel`.

### handle_start()

Records the absolute start time `t0`. Valid from LOADED or ENDED (resets engine if ENDED). Transitions to PLAYING.

### handle_jump()

Resets the engine and sets a new time origin + generation counter. If previously PLAYING, stays PLAYING (next tick renders from new timebase). If LOADED/PAUSED/ENDED, renders one frame at the exact target time, then transitions to PAUSED. Ignores if `t_rel >= _duration` or no engine loaded.

### handle_pause()

Captures `current_t_rel()`, transitions from PLAYING to PAUSED. Reports paused position via telemetry.

### handle_resume()

Sets a new `_t0` (shared time origin) without resetting the engine — preserves all cursor positions and animation state. Transitions from PAUSED to PLAYING.

### handle_stop()

Resets the engine, clears rgb buffer to black, outputs the black frame, resets `_frame_index` and `_paused_t_rel`. Transitions to LOADED. Ignored if IDLE.

### tick_once()

IDLE/ENDED → returns false. LOADED/PAUSED → returns true (alive but not advancing). PLAYING → computes `t_rel` from `now_mono() + _sync_offset - _t0`, calls `engine.tick()`, outputs frame, increments `_frame_index`. If `t_rel < 0` (future start), returns true without ticking. If engine returns false (program ended), clears to black, outputs the black frame, and transitions to ENDED.

---

## ESPDevice (planned — real hardware subclass)

> **Not yet implemented.** The sketch below outlines the intended design for the real ESP32 subclass. Only `ESPSimulated` is implemented today.

Would run on ESP32 under Arduino framework, using `esp_timer_get_time()` (monotonic µs) for time and FastLED for output:

```cpp
class ESPDevice : public PlaybackDevice {
public:
    ESPDevice(uint16_t strip_length)
        : PlaybackDevice(strip_length, /*gamma_enabled=*/true) {}

protected:
    int64_t now_mono() const override {
        return esp_timer_get_time();
    }

    void output_frame(float) override {
        FastLED.show();
    }

    void send_telemetry(DeviceState s, float t, const char* err) override {
        // Send status over UDP to controller
    }
};
```

---

## ESPSimulated (simulator subclass)

In-process desktop simulator with queue-based frame/telemetry capture. Uses `steady_clock` for time, gamma disabled. ESPSimulated itself remains transport-free. A dedicated transport process (`network_sim`) provides TCP command input and UDP frame output.

```cpp
struct SimRgbFrame {
    uint16_t gen;
    uint32_t frame_index;
    float t_rel;
    std::vector<uint8_t> rgb;
};

struct SimTelemetry {
    DeviceState state;
    float t_rel;
    std::string error;
};

class ESPSimulated : public PlaybackDevice {
public:
    explicit ESPSimulated(uint16_t strip_length);

    // Drain queued frames/telemetry — destructive, order-preserving
    std::vector<SimRgbFrame> drain_frames();
    std::vector<SimTelemetry> drain_telemetry();

    // Debug extensions (simulator only)
    void debug_seek(float target_t_rel);
    void debug_step(int direction);

protected:
    int64_t now_mono() const override;
    void output_frame(float t_rel) override;
    void send_telemetry(DeviceState s, float t, const char* err = nullptr) override;
};
```

`output_frame()` pushes a `SimRgbFrame` (gen, frame_index, t_rel, rgb copy) to an internal queue. `send_telemetry()` pushes a `SimTelemetry` to a separate queue. Callers drain queues with `drain_frames()` / `drain_telemetry()`.

`debug_seek()` replays the engine from t=0 to the target time at 50Hz steps, emits one frame, then adjusts state (stays PLAYING with adjusted `_t0`, or transitions to PAUSED). `debug_step()` advances by ±1/50s from the current paused position, valid only from PAUSED or LOADED.
