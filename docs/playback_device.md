# PlaybackDevice — Shared Device Abstraction

## Motivation

The Elements v2 system has two runtime targets for the animation engine:

1. **Real ESP32 hardware** — receives a blob over the network, decodes it, runs the engine, drives physical LEDs via FastLED.
2. **Simulated ESP** — a desktop process that runs the same engine locally, sends its RGB strip buffer back over the network as telemetry.

Both share a large amount of functionality: blob loading, program decoding, Engine+Strip lifecycle, per-frame tick logic, state management (idle → loaded → playing → paused → ended), and telemetry reporting. The differences are narrow: clock source, output method, transport, and a few simulator-only debug extensions.

Rather than duplicate this logic, a base class `PlaybackDevice` captures the shared behavior. Two subclasses — `ESPDevice` (real hardware) and `ESPSimulated` (desktop simulator) — implement the platform-specific parts via virtual methods.

---

## System context

```
                        Controller
                       (has blobs, controls time, hosts web UI)
                      /            \
              LOAD/START          LOAD/START
              (same protocol)     (same protocol)
                /                    \
        ESPDevice #1            ESPSimulated #1
        (real hardware)         (desktop process)
        - NTP-synced clock      - desktop clock (or NTP)
        - FastLED output        - sends rgb over network
        - MQTT/UDP commands     - same protocol + debug extensions
        - sends telemetry       - sends telemetry + rgb frames
```

The controller orchestrates all devices. It doesn't know or care whether a device is real or simulated — it sends the same LOAD and START commands. The only difference is that it knows simulated devices send rgb frame telemetry and accept debug extensions (pause/seek/step).

---

## The PlaybackDevice base class

### Responsibilities

- Owns the Engine, Strip, Program, and rgb buffer lifecycle
- Provides `handle_load()` / `handle_start()` for command processing
- Provides `tick_once()` — the shared per-iteration logic
- Manages device state (IDLE → LOADED → PLAYING → PAUSED → ENDED)
- Defines virtual methods for platform-specific behavior

### State machine

```
       LOAD              START             tick returns false
  IDLE ────> LOADED ────────> PLAYING ──────────> ENDED
    ^          ^                │   ^               │
    │          │ LOAD           │   │ RESUME        │ LOAD
    │          ├────────────────┤   │               │
    │          │                │   └───────┐       │
    │          │           PAUSE│           │       │
    │          │                v           │       │
    │          │             PAUSED ────────┘       │
    │          │                │                   │
    │          │ LOAD           │ LOAD              │
    └──────────┴────────────────┴───────────────────┘
```

A new LOAD at any point tears down the current program and replaces it. This is how the base station transitions between songs or back to ambient mode. PAUSE and RESUME are debug extensions available only on ESPSimulated.

### Class outline

```cpp
class PlaybackDevice {
public:
    PlaybackDevice(uint16_t strip_length, bool gamma_enabled = true);
    virtual ~PlaybackDevice();

    // --- Command handlers (called by subclass when a command arrives) ---

    // Load a new program. Tears down any existing program first.
    // Returns true on success, false on decode error.
    bool handle_load(const uint8_t* blob, size_t blob_len);

    // Begin playback. t0_epoch is the absolute start time (epoch seconds, double).
    // The device computes t_rel = now_epoch() - t0_epoch each frame.
    void handle_start(double t0_epoch);

    // --- Per-iteration logic (called from platform loop) ---

    // Run one tick: advance time, call engine, output frame.
    // Returns true if the device is still active (playing or loaded).
    // Returns false if the program has ended.
    bool tick_once();

    // --- State queries ---

    enum State { IDLE, LOADED, PLAYING, PAUSED, ENDED };
    State state() const { return _state; }
    float current_t_rel() const;
    float duration() const;

protected:
    // --- Platform-specific (virtual, implemented by subclasses) ---

    // Return current time as epoch seconds (double precision).
    // Real ESP: millis()-based NTP-synced clock.
    // Simulator: clock_gettime() or similar.
    virtual double now_epoch() = 0;

    // Output the current frame.
    // Real ESP: FastLED.show()
    // Simulator: send rgb buffer over network.
    virtual void output_frame() = 0;

    // Send telemetry/status to the controller.
    virtual void send_telemetry(State state, float t_rel,
                                const char* error = nullptr) = 0;

    // --- Internals ---

    uint16_t _strip_length;
    uint8_t* _rgb_buf;       // owned, allocated in constructor
    Strip* _strip;            // view over _rgb_buf
    Engine* _engine;          // owns Program*, created on handle_load
    State _state;
    double _t0_epoch;         // absolute start time from handle_start
    bool _gamma_enabled;
};
```

### Key method implementations

#### `handle_load()`

Tears down any existing program, decodes the new blob, creates Engine+Strip.

```cpp
bool PlaybackDevice::handle_load(const uint8_t* blob, size_t blob_len) {
    // Tear down existing
    if (_engine) {
        delete _engine;    // Engine destructor calls free_program()
        _engine = nullptr;
    }

    // Decode
    Program* prog = decode_program(blob, blob_len);
    if (!prog) {
        _state = IDLE;
        send_telemetry(IDLE, 0.0f, "decode failed");
        return false;
    }

    // Create engine (strip already exists, wrapping _rgb_buf)
    _engine = new Engine(prog, *_strip, _gamma_enabled);
    _state = LOADED;
    send_telemetry(LOADED, 0.0f);
    return true;
}
```

#### `handle_start()`

Records the absolute start time. Playback begins on the next `tick_once()`.

```cpp
void PlaybackDevice::handle_start(double t0_epoch) {
    if (_state != LOADED && _state != PAUSED && _state != ENDED)
        return;  // ignore if no program loaded
    _t0_epoch = t0_epoch;
    _state = PLAYING;
    send_telemetry(PLAYING, 0.0f);
}
```

#### `tick_once()`

The shared per-iteration logic. Called by each platform's loop.

```cpp
bool PlaybackDevice::tick_once() {
    if (_state != PLAYING)
        return _state != IDLE;  // LOADED, PAUSED, or ENDED: still "active" but not ticking

    double now = now_epoch();                  // virtual — platform clock
    float t_rel = (float)(now - _t0_epoch);

    if (!_engine->tick(t_rel)) {
        _state = ENDED;
        send_telemetry(ENDED, t_rel);
        return false;
    }

    output_frame();                            // virtual — FastLED or send rgb
    return true;
}
```

---

## ESPDevice (real hardware subclass)

Runs on ESP32 under Arduino framework. Uses NTP-synced `millis()` for time, FastLED for output, MQTT or UDP for commands.

```cpp
class ESPDevice : public PlaybackDevice {
public:
    ESPDevice(uint16_t strip_length)
        : PlaybackDevice(strip_length, /*gamma_enabled=*/true) {
        // Hardware init: FastLED, WiFi, NTP, MQTT
    }

protected:
    double now_epoch() override {
        // millis() gives ms since boot. NTP offset gives epoch alignment.
        return _ntp_epoch_offset + (millis() / 1000.0);
    }

    void output_frame() override {
        // _rgb_buf already contains the composited RGB.
        // FastLED's CRGB array points to the same buffer.
        FastLED.show();
    }

    void send_telemetry(State state, float t_rel, const char* error) override {
        // Send status over MQTT/UDP to controller
    }
};
```

Arduino integration:

```cpp
ESPDevice device(NUM_LEDS);

void setup() {
    // WiFi, NTP, MQTT init
    // FastLED.addLeds<WS2811, PIN, GRB>((CRGB*)device.rgb_buf(), NUM_LEDS);
}

void loop() {
    poll_commands();       // check MQTT for LOAD/START, call device.handle_load/start
    device.tick_once();
    // Arduino yields between loop() calls — no explicit sleep
}
```

---

## ESPSimulated (simulator subclass)

Runs as a desktop process. Uses `clock_gettime()` for time, sends rgb buffer over network, gamma disabled.

```cpp
class ESPSimulated : public PlaybackDevice {
public:
    ESPSimulated(uint16_t strip_length)
        : PlaybackDevice(strip_length, /*gamma_enabled=*/false) {}

protected:
    double now_epoch() override {
        struct timespec ts;
        clock_gettime(CLOCK_REALTIME, &ts);
        return ts.tv_sec + ts.tv_nsec / 1e9;
    }

    void output_frame() override {
        // Send rgb buffer as telemetry to controller
        send_rgb_frame(_rgb_buf, _strip_length * 3);
    }

    void send_telemetry(State state, float t_rel, const char* error) override {
        // Send status over UDP to controller
    }

public:
    // --- Debug extensions (simulator only) ---

    void debug_pause() {
        if (_state == PLAYING) {
            _paused_t_rel = current_t_rel();
            _state = PAUSED;
        }
    }

    void debug_resume() {
        if (_state == PAUSED) {
            // Adjust t0 so that now_epoch() - _t0_epoch == _paused_t_rel
            _t0_epoch = now_epoch() - (double)_paused_t_rel;
            _state = PLAYING;
        }
    }

    void debug_seek(float target_t_rel) {
        if (!_engine) return;
        _engine->reset();

        // Replay frame-by-frame from 0 to target
        float dt = 1.0f / 50.0f;
        for (float t = dt; t < target_t_rel; t += dt)
            _engine->tick(t);
        _engine->tick(target_t_rel);

        output_frame();

        if (_state == PLAYING) {
            // Adjust t0 so playback continues from seek point
            _t0_epoch = now_epoch() - (double)target_t_rel;
        } else {
            // PAUSED or LOADED — stay paused at seek point
            _paused_t_rel = target_t_rel;
            _state = PAUSED;
        }
    }

    void debug_step(int direction) {
        // Step only makes sense when paused
        if (_state != PAUSED) return;

        float target = _paused_t_rel + direction * (1.0f / 50.0f);
        if (target < 0.0f) target = 0.0f;

        _engine->reset();
        float dt = 1.0f / 50.0f;
        for (float t = dt; t < target; t += dt)
            _engine->tick(t);
        _engine->tick(target);

        _paused_t_rel = target;
        output_frame();
    }

    // Access to rgb buffer for the controller/telemetry
    const uint8_t* rgb_buf() const { return _rgb_buf; }

private:
    float _paused_t_rel = 0.0f;
};
```

Desktop main loop:

```cpp
ESPSimulated device(NUM_LEDS);

int main() {
    // Parse args, set up UDP listener for commands

    auto next_tick = steady_clock::now();
    auto dt = chrono::milliseconds(20);  // 50Hz

    while (running) {
        poll_commands();        // check for LOAD/START/debug commands
        device.tick_once();

        // Pace + overrun detection
        next_tick += dt;
        auto now = steady_clock::now();
        if (now > next_tick) {
            log_overrun(now - next_tick + dt);
            next_tick = now;    // don't try to catch up
        } else {
            this_thread::sleep_until(next_tick);
        }
    }
}
```

---

## Clock and time model

### Absolute vs relative time

Two time representations serve different purposes:

- **Absolute time (double, epoch seconds):** Used in the protocol between controller and devices. The START command carries a `t0_epoch` value. Devices compute `t_rel = now_epoch - t0_epoch`. This is what enables multi-device synchronization — all devices derive the same `t_rel` from the same absolute reference.

- **Relative time (float, seconds since program start):** Used inside the engine. `engine.tick(t_rel)` takes a float. This is fine for durations up to hours — float32 precision at 600 seconds (10 min) is ~40 microseconds, far below the 20ms frame interval.

### Why double for epoch timestamps

Current epoch is ~1.74 billion seconds. float32 has ~7 significant digits, giving a minimum step of ~128 seconds at current epoch — useless for millisecond precision. float64 (double) has ~15 significant digits, giving sub-microsecond precision. The conversion to float happens only when computing `t_rel`, where the magnitude is small enough for float32.

### Simulator clock

ESPSimulated uses `clock_gettime(CLOCK_REALTIME)` which returns epoch time. It can optionally sync to the same NTP server as real ESPs, enabling mixed real+simulated setups on a LAN where timing accuracy matters.

For pure local preview (no real ESPs), the absolute epoch value doesn't matter — the device just computes `t_rel = now - t0` and the offset cancels out.

### Seek and virtual time

When ESPSimulated receives a debug_seek command, it:
1. Calls `engine->reset()` (zeros cursors, instances, buffers)
2. Replays from t=0 to target in dt steps (tight C++ loop, microseconds)
3. Adjusts `_t0_epoch` so that `now_epoch() - _t0_epoch == target_t_rel`

If the device resumes playing after seek, the clock continues naturally from the seek point. If it's paused, `_paused_t_rel` tracks the virtual position.

---

## Gamma strategy

The Compositor currently applies gamma correction unconditionally. This is correct for physical LEDs (WS2811 expects gamma-corrected values) but wrong for a monitor display (which applies its own gamma).

The fix: add a `bool gamma_enabled` flag to the Compositor, passed through from the Engine constructor. PlaybackDevice passes this flag when creating the Engine.

- `ESPDevice`: `gamma_enabled = true` (hardware LEDs)
- `ESPSimulated`: `gamma_enabled = false` (monitor display via browser)

Implementation in Compositor:

```cpp
// In Compositor::composite(), wrap the gamma loop:
if (_gamma_enabled) {
    for (uint16_t i = 0; i < _strip.length(); i++)
        _strip.set_rgb(i, gamma_correct(_strip.get_rgb(i)));
}
```

---

## Engine::reset() — required new method

Needed for seek/step/loop in the simulator. The engine's cursor is forward-only; `tick(t)` assumes monotonically increasing time. To seek backward (or forward safely), the engine must return to its initial state and replay.

```cpp
void Engine::reset() {
    for (uint8_t i = 0; i < _prog->layer_count; i++) {
        delete _states[i].instance;
        _states[i].instance = nullptr;
        _states[i].cursor = 0;
        memset(_prog->layers[i].buffer, 0,
               _prog->layers[i].index_map_length * sizeof(hsva_t));
    }
    // Zero buffer pool (shift work buffers)
    for (uint8_t i = 0; i < _prog->pool.count; i++) {
        memset(_prog->pool.buffers[i], 0,
               _prog->pool.sizes[i] * sizeof(hsva_t));
    }
}
```

After reset, the engine is in the same state as immediately after construction. Source-dependent animations (shift snapshotting a source layer) reproduce correctly because the source layer is re-rendered during replay.

---

## Controller responsibilities

The controller is a separate component that orchestrates all devices. It:

1. **Holds the blobs** — produced by the compiler, one per strip.
2. **Maps blobs to devices** — knows which device runs which strip.
3. **Sends LOAD** — delivers blob bytes to each device.
4. **Sends START with shared T0** — all devices begin playback from the same absolute time, so animations synchronize across strips.
5. **Receives telemetry** — health, errors, timing from all devices.
6. **Receives rgb frames from simulators** — forwards to the web UI for display.
7. **Hosts the web UI** (open issue — see below).

The controller uses the same protocol for real and simulated devices. It knows which devices are simulated and can:
- Send debug extensions (pause/seek/step) to simulators
- Expect rgb frame telemetry from simulators
- Route rgb frames to the browser UI

---

## Data flow: end-to-end example

A wave animation on 10 pixels, controller + one ESPSimulated, browser display.

### 1. Startup

```
Controller                     ESPSimulated (separate process)
   │                              │
   │  LOAD(blob_bytes)            │
   │─────────────────────────────>│
   │                              │  handle_load():
   │                              │    decode_program() → Program*
   │                              │    allocate rgb_buf (30 bytes)
   │                              │    create Strip(rgb_buf, 10)
   │                              │    create Engine(prog, strip, gamma=false)
   │                              │    state = LOADED
   │                              │
   │  START(t0_epoch=1741203600.0)│
   │─────────────────────────────>│
   │                              │  handle_start(t0_epoch):
   │                              │    _t0_epoch = 1741203600.0
   │                              │    state = PLAYING
```

### 2. Playback (ESPSimulated main loop, autonomous)

```
ESPSimulated loop iteration:
   │
   │  now = clock_gettime() → 1741203600.3
   │  t_rel = 1741203600.3 - 1741203600.0 = 0.3
   │
   │  engine.tick(0.3)
   │    → AnimWave::render() fills layer buffer
   │    → Compositor blends into rgb_buf (no gamma)
   │    → rgb_buf = [0,1,5, 0,4,97, 0,1,5, ...]
   │
   │  output_frame()
   │    → send rgb_buf (30 bytes) + t_rel (0.3) to controller via UDP
   │
   │  sleep until next frame (20ms cadence)
```

### 3. Controller receives and forwards to browser

```
ESPSimulated              Controller                    Browser
   │                         │                            │
   │  [rgb + t_rel]          │                            │
   │────────────────────────>│                            │
   │         (UDP)           │  [strip_id + t + rgb]      │
   │                         │───────────────────────────>│
   │                         │       (WebSocket)          │
   │                         │                            │  render canvas
   │                         │                            │  update clock
```

### 4. Seek (user drags scrub bar)

```
Browser                   Controller                ESPSimulated
   │                         │                         │
   │ {"cmd":"seek","t":2.5}  │                         │
   │────────────────────────>│                         │
   │                         │  DEBUG_SEEK(2.5)        │
   │                         │────────────────────────>│
   │                         │                         │  debug_seek(2.5):
   │                         │                         │    engine.reset()
   │                         │                         │    replay 0→2.5
   │                         │                         │    adjust t0_epoch
   │                         │                         │    output_frame()
   │                         │                         │
   │                         │  [rgb + t_rel=2.5]      │
   │                         │<────────────────────────│
   │  [strip_id + 2.5 + rgb] │                         │
   │<────────────────────────│                         │
   │                         │                         │
   │  clock: "0:02.50"       │                         │
```

---

## Open issues / undecided

### 1. Transport protocol

The production path uses MQTT (per `docs/v2_design.md`). For the simulator on localhost, MQTT adds a broker dependency. Options under consideration:
- MQTT everywhere (mosquitto is lightweight)
- Raw UDP for both
- MQTT for production, UDP for simulator (same message format, different transport)

This affects the `receive_command()` / `send_telemetry()` virtual methods and the controller's transport layer. **Decision deferred.**

### 2. Controller ↔ Web UI relationship

The controller receives rgb frame telemetry from simulators and needs to forward it to the browser. Questions:
- Does the controller host the web UI directly (Flask + WebSocket)?
- Or is the web UI a separate service that connects to the controller?
- How does the browser discover which strips exist and their layout?

The controller is the natural host since it already knows about all devices. But the exact architecture (Flask in the controller process, or a separate frontend server) is **not yet decided.**

### 3. Multi-device seek/pause coordination

When the user seeks or pauses via the browser, the controller sends debug commands to all simulated devices. Questions:
- Should all simulators seek atomically (all get the command before any resume)?
- What happens if some devices are real and some simulated — pause only affects simulators while real ESPs keep playing?
- Is there a "simulation mode" where the controller knows all devices are simulated and enables full debug control?

**Not yet decided.**

### 4. Program looping

When a program ends (`tick()` returns false), what happens?
- The device transitions to ENDED state and goes dark.
- The controller can send a new LOAD to restart, or the device could auto-loop.
- For the simulator, auto-loop is useful for preview. For production, the controller decides.

Should looping be a device-level setting (passed with LOAD or START) or a controller-level concern? **Not yet decided.**

### 5. ESPSimulated as separate process — startup and discovery

ESPSimulated runs as its own process. How does the controller discover it?
- Controller starts the process and knows the port?
- ESPSimulated announces itself on startup (multicast/broadcast)?
- Static config file listing devices and their addresses?

**Not yet decided.**
