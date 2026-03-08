# PlaybackDevice — Shared Device Abstraction

> **Status: Implemented.** Base class in `src/playback_device.h` / `src/playback_device.cpp`. Tests in `test/test_playback_device.cpp`. Subclasses (`ESPDevice`, `ESPSimulated`) are not yet implemented.
>
> Related docs: `transport.md` (device protocol, clock sync), `controller.md` (controller/web-app architecture, identity model, reset-safe intervals).

## Overview

Both runtime targets (real ESP32 and desktop simulator) share the same playback logic: blob loading, Engine/Strip lifecycle, per-frame tick, and state management. The base class `PlaybackDevice` captures this shared behavior. Two subclasses — `ESPDevice` (real hardware) and `ESPSimulated` (desktop simulator) — implement the platform-specific parts via virtual methods.

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
    virtual void output_frame() = 0;           // push rgb buffer to output
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

## ESPDevice (real hardware subclass)

Runs on ESP32 under Arduino framework. Uses `esp_timer_get_time()` (monotonic µs) for time, FastLED for output.

```cpp
class ESPDevice : public PlaybackDevice {
public:
    ESPDevice(uint16_t strip_length)
        : PlaybackDevice(strip_length, /*gamma_enabled=*/true) {}

protected:
    int64_t now_mono() const override {
        return esp_timer_get_time();
    }

    void output_frame() override {
        FastLED.show();
    }

    void send_telemetry(DeviceState s, float t, const char* err) override {
        // Send status over UDP to controller
    }
};
```

Arduino integration:

```cpp
ESPDevice device(NUM_LEDS);

void setup() {
    // WiFi, FastLED, TCP server, UDP socket
    // FastLED.addLeds<WS2811, PIN, GRB>((CRGB*)device.rgb_buf(), NUM_LEDS);
}

void loop() {
    poll_tcp_commands(tcp_fd, device);
    poll_udp_sync(udp_fd);
    device.tick_once();
}
```

---

## ESPSimulated (simulator subclass)

Runs as a desktop process. Uses `steady_clock` for time, sends rgb buffer over UDP, gamma disabled.

```cpp
class ESPSimulated : public PlaybackDevice {
public:
    ESPSimulated(uint16_t strip_length)
        : PlaybackDevice(strip_length, /*gamma_enabled=*/false) {}

protected:
    int64_t now_mono() const override {
        auto now = steady_clock::now();
        return duration_cast<microseconds>(now.time_since_epoch()).count();
    }

    void output_frame() override {
        send_rgb_frame(_gen, _frame_index, _rgb_buf, _strip_length * 3);
    }

    void send_telemetry(DeviceState s, float t, const char* err) override {
        // Send status over UDP to controller
    }

public:
    // --- Debug extensions (simulator only) ---

    void debug_seek(float target_t_rel) {
        if (!_engine) return;
        float dur = duration();
        if (target_t_rel < 0.0f) target_t_rel = 0.0f;
        if (target_t_rel > dur) target_t_rel = dur;

        _engine->reset();
        float dt = 1.0f / 50.0f;
        for (float t = dt; t < target_t_rel; t += dt)
            _engine->tick(t);
        _engine->tick(target_t_rel);
        output_frame();
        _frame_index++;

        if (_state == DeviceState::PLAYING) {
            _t0 = now_mono() + _sync_offset - (int64_t)(target_t_rel * 1e6f);
        } else {
            _paused_t_rel = target_t_rel;
            _state = DeviceState::PAUSED;
        }
    }

    void debug_step(int direction) {
        if (_state != DeviceState::PAUSED && _state != DeviceState::LOADED) return;
        float target = _paused_t_rel + direction * (1.0f / 50.0f);
        if (target < 0.0f) target = 0.0f;
        if (target > duration()) target = duration();

        _engine->reset();
        float dt = 1.0f / 50.0f;
        for (float t = dt; t < target; t += dt)
            _engine->tick(t);
        _engine->tick(target);

        _paused_t_rel = target;
        _state = DeviceState::PAUSED;
        output_frame();
        _frame_index++;
    }
};
```

Desktop main loop:

```cpp
ESPSimulated device(NUM_LEDS);

int main() {
    auto next_tick = steady_clock::now();
    auto dt = chrono::milliseconds(20);  // 50Hz

    while (running) {
        poll_tcp_commands(tcp_fd, device);
        poll_udp_sync(udp_fd);
        device.tick_once();

        next_tick += dt;
        auto now = steady_clock::now();
        if (now > next_tick) {
            log_overrun(now - next_tick + dt);
            next_tick = now;
        } else {
            this_thread::sleep_until(next_tick);
        }
    }
}
```
