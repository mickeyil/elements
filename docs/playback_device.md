# PlaybackDevice — Shared Device Abstraction

## Motivation

The Elements system has two runtime targets for the animation engine:

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
        - controller-synced clock - desktop monotonic clock
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

    // Begin playback. t0 is the absolute start time (int64_t, microseconds).
    // The device computes t_rel = (now_mono() + _sync_offset - t0) / 1e6 each frame.
    void handle_start(int64_t t0);

    // Update sync offset (from controller SYNC_RESULT).
    void handle_sync_result(int64_t offset);

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

    // Return current monotonic time in microseconds (int64_t).
    // Real ESP: esp_timer_get_time().
    // Simulator: steady_clock.
    virtual int64_t now_mono() = 0;

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
    State _state = IDLE;
    int64_t _t0 = 0;              // absolute start time from handle_start (µs)
    int64_t _sync_offset = 0;     // controller-provided offset (µs), 0 until first SYNC_RESULT
    bool _gamma_enabled;
};
```

`_sync_offset = 0` means uncorrected local monotonic time — safe as a default since playback won't start until after `handle_start()`, and by that point the controller should have completed at least one sync round. If sync hasn't happened yet, playback proceeds with uncorrected local time (drift accumulates but no crash or garbage values).

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
void PlaybackDevice::handle_start(int64_t t0) {
    if (_state != LOADED && _state != PAUSED && _state != ENDED)
        return;  // ignore if no program loaded

    if (_state == PAUSED || _state == ENDED)
        _engine->reset();

    _t0 = t0;
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

    int64_t now = now_mono() + _sync_offset;    // virtual mono + controller offset
    float t_rel = (float)(now - _t0) / 1e6f;  // µs → seconds

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

Runs on ESP32 under Arduino framework. Uses `esp_timer_get_time()` (monotonic µs) + controller-provided sync offset for time, FastLED for output, UDP for commands.

```cpp
class ESPDevice : public PlaybackDevice {
public:
    ESPDevice(uint16_t strip_length)
        : PlaybackDevice(strip_length, /*gamma_enabled=*/true) {
        // Hardware init: FastLED, WiFi, UDP
    }

protected:
    int64_t now_mono() override {
        return esp_timer_get_time();  // microseconds, monotonic
    }

    void output_frame() override {
        // _rgb_buf already contains the composited RGB.
        // FastLED's CRGB array points to the same buffer.
        FastLED.show();
    }

    void send_telemetry(State state, float t_rel, const char* error) override {
        // Send status over UDP to controller
    }
};
```

Arduino integration:

```cpp
ESPDevice device(NUM_LEDS);

void setup() {
    // WiFi, UDP init (no NTP needed — controller handles sync)
    // FastLED.addLeds<WS2811, PIN, GRB>((CRGB*)device.rgb_buf(), NUM_LEDS);
}

void loop() {
    poll_commands();       // check UDP for LOAD/START/SYNC, call device handlers
    device.tick_once();
    // Arduino yields between loop() calls — no explicit sleep
}
```

---

## ESPSimulated (simulator subclass)

Runs as a desktop process. Uses `steady_clock` (monotonic) for time, sends rgb buffer over network, gamma disabled.

```cpp
class ESPSimulated : public PlaybackDevice {
public:
    ESPSimulated(uint16_t strip_length)
        : PlaybackDevice(strip_length, /*gamma_enabled=*/false) {}

protected:
    int64_t now_mono() override {
        auto now = steady_clock::now();
        return duration_cast<microseconds>(now.time_since_epoch()).count();
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
            // Adjust t0 so that (now_mono() + offset - t0) / 1e6 == _paused_t_rel
            _t0 = now_mono() + _sync_offset - (int64_t)(_paused_t_rel * 1e6f);
            _state = PLAYING;
        }
    }

    void debug_seek(float target_t_rel) {
        if (!_engine) return;

        // Clamp to valid range
        float dur = duration();
        if (target_t_rel < 0.0f) target_t_rel = 0.0f;
        if (target_t_rel > dur) target_t_rel = dur;

        _engine->reset();

        // Replay frame-by-frame from 0 to target.
        // At target == duration the final tick() returns false, but
        // the strip buffer still holds the last rendered frame — this
        // is intentional (seek-to-end shows the final frame, not blank).
        float dt = 1.0f / 50.0f;
        for (float t = dt; t < target_t_rel; t += dt)
            _engine->tick(t);
        _engine->tick(target_t_rel);

        output_frame();

        if (_state == PLAYING) {
            // Adjust t0 so playback continues from seek point
            _t0 = now_mono() + _sync_offset - (int64_t)(target_t_rel * 1e6f);
        } else {
            // PAUSED or LOADED — stay paused at seek point
            _paused_t_rel = target_t_rel;
            _state = PAUSED;
        }
    }

    void debug_step(int direction) {
        // Step from PAUSED or LOADED (LOADED treated as "paused at t=0")
        if (_state != PAUSED && _state != LOADED) return;

        float dur = duration();
        float target = _paused_t_rel + direction * (1.0f / 50.0f);
        if (target < 0.0f) target = 0.0f;
        if (target > dur) target = dur;

        _engine->reset();
        float dt = 1.0f / 50.0f;
        for (float t = dt; t < target; t += dt)
            _engine->tick(t);
        _engine->tick(target);

        _paused_t_rel = target;
        _state = PAUSED;  // LOADED → PAUSED on first step
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

This is a clock subsystem and command-protocol feature. The engine is not involved — it still receives only `float t_rel` and knows nothing about synchronization. Only the time origination path changes.

### Absolute vs relative time

Two time representations serve different purposes:

- **Absolute time (int64_t, microseconds):** Used in the protocol between controller and devices. The START command carries a `t0` value. Devices compute `t_rel = (now_mono + sync_offset - t0) / 1e6`. This is what enables multi-device synchronization — all devices derive the same `t_rel` from the same reference.

- **Relative time (float, seconds since program start):** Used inside the engine. `engine.tick(t_rel)` takes a float. This is fine for durations up to hours — float32 precision at 600 seconds (10 min) is ~40 microseconds, far below the 20ms frame interval.

### Clock architecture

Each device maintains a monotonic local clock and a sync offset provided by the controller:

```
now_epoch_approx = now_mono + sync_offset
t_rel = (now_epoch_approx - t0) / 1e6        (microseconds → float seconds)
```

- **ESP:** `now_mono` = `esp_timer_get_time()` (microsecond monotonic). No epoch knowledge needed — the controller provides `sync_offset`.
- **Simulator:** `now_mono` = `steady_clock` (monotonic, immune to wall-clock/NTP jumps on host). For local-only use, `sync_offset = 0` — the controller and simulator share the same clock domain.
- **Controller:** uses its own monotonic clock as the reference. Epoch alignment is irrelevant — what matters is stability and consistency across devices.

All protocol timestamps are **int64_t microseconds** — avoids float byte-order issues and keeps deterministic precision.

### Custom sync protocol (replaces NTP on ESP)

Instead of each ESP running an NTP client, the controller performs a lightweight sync exchange over the existing UDP command channel.

**Why not NTP:**
- NTPClient on ESP is fragile: `forceUpdate()` blocks up to 1s, `getEpochTime()` loses sub-second precision via integer division, managing the library is unnecessary complexity.
- The controller already talks to every device. Adding sync to the existing protocol is natural.
- The controller can centrally track sync quality per device and make informed decisions.

#### Sync exchange

```
Controller                           ESP
    │                                 │
    │  SYNC_REQ { seq, t1 }         │
    │────────────────────────────────>│
    │                                 │  t2 = esp_timer_get_time()
    │  SYNC_RESP { seq, t1, t2, t3 }│  t3 = esp_timer_get_time()
    │<────────────────────────────────│
    │  t4 = mono_now()               │
    │                                 │
    │  rtt = (t4 - t1) - (t3 - t2)
    │  offset = ((t2 - t1) + (t3 - t4)) / 2
```

- **T1, T4:** controller monotonic timestamps (int64_t µs).
- **T2, T3:** ESP monotonic timestamps (int64_t µs) — both from `esp_timer_get_time()`, same clock source.
- **seq:** uint16_t sequence number — controller ignores stale/mismatched replies.
- **boot_seq:** uint32_t boot counter — ensures offsets from a pre-reboot timer are not reused.

Packet format (all fields little-endian):

```
SYNC_REQ:   [type: u8 = 0x01] [seq: u16] [boot_seq: u32] [t1: i64]     → 15 bytes
SYNC_RESP:  [type: u8 = 0x02] [seq: u16] [boot_seq: u32] [t1: i64] [t2: i64] [t3: i64]  → 31 bytes
SYNC_RESULT:[type: u8 = 0x03] [seq: u16] [boot_seq: u32] [offset: i64]  → 15 bytes
```

#### ESP implementation (minimal, non-blocking)

```cpp
int64_t _sync_offset = 0;    // set by controller via SYNC_RESULT
uint32_t _boot_seq = 0;     // incremented on each boot
uint16_t _last_sync_seq = 0; // last applied SYNC_RESULT seq

void handle_sync_req(const uint8_t* pkt) {
    int64_t t2 = esp_timer_get_time();

    SyncResp resp;
    memcpy(&resp.seq, pkt + 1, 2);
    memcpy(&resp.boot_seq, pkt + 3, 4);
    memcpy(&resp.t1, pkt + 7, 8);
    resp.t2 = t2;
    resp.t3 = esp_timer_get_time();
    send_udp(&resp, sizeof(resp));
}

void handle_sync_result(const uint8_t* pkt) {
    uint16_t seq;
    uint32_t boot_seq;
    memcpy(&seq, pkt + 1, 2);
    memcpy(&boot_seq, pkt + 3, 4);

    // Drop stale: wrong boot epoch or old/reordered sequence
    if (boot_seq != _boot_seq || seq < _last_sync_seq)
        return;

    _last_sync_seq = seq;
    memcpy(&_sync_offset, pkt + 7, 8);
}

int64_t now_epoch_approx() {
    return esp_timer_get_time() + _sync_offset;
}
```

No NTP library. No blocking. No state machine. The ESP is a passive responder — it timestamps and echoes. The controller owns all filtering and correction logic.

#### Controller sync policy

**Startup (before first LOAD):**

1. Send 8 SYNC_REQ rounds at 1-second intervals.
2. For each round, compute RTT and offset.
3. Filter: discard samples where `rtt > rtt_max` or `rtt < 0`.
4. Sort remaining by RTT, keep lowest K (e.g., K=3–4) — low RTT means less asymmetric jitter.
5. Compute median offset of those K candidates.
6. Send SYNC_RESULT to ESP.

**Steady-state (during and between playback):**

Periodic probes at an adaptive interval:

| Condition | Probe interval |
|-----------|---------------|
| Startup calibration | 1s (8 rounds) |
| Steady, good confidence | 15s |
| Poor confidence or high variance | 5–10s |
| Idle (no playback, low priority) | 20–30s |

At ~10 ESP devices, even 10s intervals are negligible network load (~31 bytes per probe).

#### Controller filter model

```python
class DeviceSync:
    WINDOW_SIZE = 5

    def __init__(self):
        self.window = []            # median window of filtered offsets
        self.applied_offset = 0     # currently sent to ESP
        self.prev_delta_sign = 0    # for sustained-move guard

    def on_sync_sample(self, samples_from_round):
        """Called with (rtt, offset) pairs from one or more recent probes."""
        # 1. Discard invalid
        valid = [(rtt, off) for rtt, off in samples_from_round
                 if 0 < rtt < RTT_MAX]
        if not valid:
            return

        # 2. Sort by RTT, keep lowest K
        by_rtt = sorted(valid, key=lambda s: s[0])
        best = by_rtt[:min(4, len(by_rtt))]

        # 3. Median offset of best candidates
        offsets = sorted(s[1] for s in best)
        candidate = offsets[len(offsets) // 2]

        # 4. Feed into sliding median window
        self.window.append(candidate)
        if len(self.window) > self.WINDOW_SIZE:
            self.window.pop(0)
        if len(self.window) < 3:
            return  # not enough data

        smoothed = median(self.window)
        delta = smoothed - self.applied_offset

        # 5. Ignore noise floor
        if abs(delta) < 2000:   # < 2ms in µs
            self.prev_delta_sign = 0
            return

        # 6. Sustained-move guard: require 2 consecutive deltas
        #    in the same direction before applying
        sign = 1 if delta > 0 else -1
        if sign != self.prev_delta_sign:
            self.prev_delta_sign = sign
            return  # first move in this direction — wait for confirmation

        # 7. Apply
        self.applied_offset = smoothed
        send_sync_result(device, smoothed)
        self.prev_delta_sign = 0

    def is_stale(self, max_age_s=60):
        """True if no successful sync in max_age_s — trigger full re-sync."""
        ...
```

Key properties of this filter:
- **Low-RTT selection** removes WiFi retransmit noise.
- **Median window** (N=5) makes a single outlier unable to move the output.
- **Sustained-move guard** requires two consecutive deltas in the same direction before applying — prevents toggling from a jitter spike that happens to survive the median.
- **Staleness timeout** triggers a full re-sync if samples are consistently bad.

#### Correction behavior during playback

When the ESP receives a SYNC_RESULT with an updated offset:

- **ESP clock is early** (offset correction makes `t_rel` smaller → animation was ahead): next `tick_once()` produces a smaller `t_rel` than expected. The engine effectively stalls for one frame (renders the same visual position twice). Invisible at 20ms frame intervals.
- **ESP clock is late** (offset correction makes `t_rel` larger → animation was behind): next `tick_once()` produces a larger `t_rel` jump. The engine's cursor naturally skips past finished events — this is a single `tick()` call, no replay needed.

Both directions are handled gracefully by the existing engine design. Corrections filtered through the median window + sustained-move guard are small (a few ms), so the frame-to-frame timing perturbation is imperceptible.

### Simulator clock

ESPSimulated uses `steady_clock` (monotonic) for its tick loop. This is immune to wall-clock jumps from NTP adjustments on the host machine.

For local-only use (no real ESPs): `sync_offset = 0`. The controller and simulator share the same clock domain, so no sync exchange is needed.

For mixed real+simulated setups: the controller can derive a trivial offset for the simulator from the identity relationship (same machine = same monotonic base). Real ESPs go through the full sync protocol.

### Seek and virtual time

When ESPSimulated receives a debug_seek command, it:
1. Calls `engine->reset()` (zeros cursors, instances, buffers)
2. Replays from t=0 to target in dt steps (tight C++ loop, microseconds)
3. Adjusts `_t0` so that `(now_mono() + _sync_offset - _t0) / 1e6 == target_t_rel`

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

1. **Syncs clocks** — runs the custom sync protocol (SYNC_REQ/RESP) with each ESP, maintains filtered offsets, sends SYNC_RESULT.
2. **Holds the blobs** — produced by the compiler, one per strip.
3. **Maps blobs to devices** — knows which device runs which strip.
4. **Sends LOAD** — delivers blob bytes to each device.
5. **Sends START with shared T0** — all devices begin playback from the same absolute time, so animations synchronize across strips.
6. **Receives telemetry** — health, errors, timing from all devices.
7. **Receives rgb frames from simulators** — forwards to the web UI for display.
8. **Hosts the web UI** (open issue — see below).

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
   │  START(t0)                   │
   │─────────────────────────────>│
   │                              │  handle_start(t0):
   │                              │    _t0 = t0
   │                              │    state = PLAYING
```

### 2. Playback (ESPSimulated main loop, autonomous)

```
ESPSimulated loop iteration:
   │
   │  now = now_mono() + _sync_offset
   │  t_rel = (now - _t0) / 1e6 → 0.3
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
   │                         │                         │    adjust _t0
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

The sync protocol uses raw UDP. The command/telemetry transport is undecided. Options under consideration:
- Raw UDP for everything (simplest, already used by sync)
- MQTT for commands/telemetry (adds broker dependency but gives pub/sub for free)
- UDP for commands, MQTT for telemetry (hybrid)

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
