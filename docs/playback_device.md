# PlaybackDevice — Shared Device Abstraction

> **Status: Design document.** Not yet implemented. Describes planned architecture for the PlaybackDevice hierarchy, transport split, custom clock sync protocol, controller/web-app separation, session identity model, reset-safe jump points, and simulator debug extensions. Prerequisites `Engine::reset()` and Compositor gamma control are implemented in `src/engine.cpp` and `src/compositor.cpp`.

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
                                     (compiles, routes blobs,
                                      syncs clocks, manages sessions)
                                    /            \
                            TCP + UDP          control/event API
                            (device protocol)  (internal, e.g. JSON/TCP)
                          /                        \
                  Devices                        Web App
                 /       \                      (thin relay,
        ESPDevice    ESPSimulated                serves browser assets)
        (real HW)    (desktop)                       |
                                                 WebSocket
                                                     |
                                                  Browser
                                                 (canvas, UI)
```

The controller is the long-running authority. It compiles programs, routes blobs to devices, manages clock sync, tracks playback state, and exposes a control/event API to clients. The web app is a separate process that relays between the controller and the browser — it does not compile, does not know device IPs, and does not manage sync.

---

## Base-station config

A static config file on the base station is the single source of truth for the physical setup. It maps logical strip names (used in the DSL) to physical devices.

### Example config

```json
{
  "strips": {
    "main_left": {
      "device_id": "esp-01",
      "ip": "192.168.1.10",
      "length": 150,
      "mode": "sim"
    },
    "main_right": {
      "device_id": "esp-02",
      "ip": "192.168.1.11",
      "length": 150,
      "mode": "sim"
    }
  },
  "simulation": {
    "layout": {
      "main_left":  { "x": 0,   "y": 0, "dx": 1, "dy": 0 },
      "main_right": { "x": 0,   "y": 2, "dx": 1, "dy": 0 }
    }
  }
}
```

### What the config provides

| Field | Used by | Purpose |
|-------|---------|---------|
| `strip_id` (key) | Controller, compiler | Logical name — matches DSL `strip("main_left", ...)` |
| `device_id` | Controller | Human-readable device name for logs/UI |
| `ip` | Controller | Where to open TCP/UDP connections |
| `length` | Controller | Validated against DSL-declared strip length at compile time |
| `mode` | Controller | `"sim"` or `"esp"` — determines seek behavior and debug capabilities |
| `simulation.layout` | Web app, browser | Pixel positions for canvas rendering |

### Relationship to the DSL

The DSL declares strip names and lengths: `strip("main_left", length=150)`. The config maps those names to physical devices. The controller validates at compile time that DSL-declared lengths match config-declared lengths. If they disagree, compilation fails with a clear error.

### Relationship to device discovery

The config replaces dynamic discovery. The controller reads it at startup and knows every device's address, role, and capabilities. ESPSimulated processes are started separately and listen on the configured IPs/ports. No multicast, no announcements — the config is the truth.

---

## Transport architecture

Three channels per device, split by requirements:

| Channel | Direction | Purpose | Why this transport |
|---------|-----------|---------|-------------------|
| **TCP** | controller → device | LOAD, START, SYNC_RESULT, JUMP, debug commands | Reliable delivery, arbitrary payload size (blobs can exceed UDP MTU) |
| **UDP inbound** | controller → device | SYNC_REQ | Low-latency RTT measurement — TCP head-of-line blocking and Nagle would corrupt offset calculations |
| **UDP outbound** | device → controller | SYNC_RESP, telemetry, RGB frames | Fire-and-forget streaming; dropped frame = browser skips one update |

Each device listens on one TCP port and one UDP port. The controller maintains a persistent TCP connection to each device and sends UDP sync probes to the device's UDP port.

### TCP command protocol

The controller opens a persistent TCP connection to each device. Commands are length-prefixed messages:

```
[length: u32 little-endian] [type: u8] [payload...]
```

`length` includes the type byte but not itself. So a START command (type + 8 bytes of t0) has `length = 9`.

Command types:

```
CMD_LOAD:         type = 0x10, payload = blob bytes (variable length)
CMD_START:        type = 0x11, payload = [t0: i64]                     -> 9 bytes total
CMD_JUMP:         type = 0x12, payload = [t0: i64] [t_rel: f32]        -> 13 bytes total
CMD_SYNC_RESULT:  type = 0x03, payload = [seq: u16] [boot_seq: u32] [offset: i64] -> 15 bytes total
CMD_DEBUG_PAUSE:  type = 0x20, no payload                              -> 1 byte total
CMD_DEBUG_RESUME: type = 0x21, no payload                              -> 1 byte total
CMD_DEBUG_SEEK:   type = 0x22, payload = [t_rel: f32]                  -> 5 bytes total
CMD_DEBUG_STEP:   type = 0x23, payload = [direction: i8]               -> 2 bytes total
```

Response (device → controller, same TCP connection):

```
CMD_ACK:          type = 0x80, payload = [status: u8]                  -> 2 bytes total
                  status: 0 = ok, 1 = decode error
```

The device sends an ACK after LOAD (with decode status). Other commands are fire-and-forget from the controller's perspective — the TCP connection itself provides delivery guarantee.

**CMD_JUMP vs CMD_DEBUG_SEEK:** CMD_JUMP is a lightweight seek available on all devices. It carries a shared absolute `t0` (like CMD_START) so all devices stay synchronized, plus `t_rel` for precise frame rendering when paused. Valid only at reset-safe jump points (see "Reset-safe jump points" below). CMD_DEBUG_SEEK is simulator-only: it replays from t=0 to the target, producing correct output at any arbitrary time.

### TCP command reading (device side)

The device reads from the TCP socket in its main loop. Commands are length-prefixed, so reading is straightforward and non-blocking:

```cpp
// Persistent read buffer — accumulates partial TCP reads across loop iterations.
// Sized for the largest expected command (LOAD with max blob size).
static uint8_t tcp_buf[32768];
static uint32_t tcp_buf_len = 0;

// Called each loop iteration. Non-blocking: reads whatever is available,
// processes complete commands, leaves partial data for next call.
void poll_tcp_commands(int tcp_fd, PlaybackDevice& device) {
    // Non-blocking read — append to buffer
    int n = recv(tcp_fd, tcp_buf + tcp_buf_len,
                 sizeof(tcp_buf) - tcp_buf_len, MSG_DONTWAIT);
    if (n > 0) tcp_buf_len += n;

    // Process complete messages
    while (tcp_buf_len >= 4) {
        uint32_t msg_len;
        memcpy(&msg_len, tcp_buf, 4);

        if (tcp_buf_len < 4 + msg_len) break;  // incomplete message

        uint8_t* msg = tcp_buf + 4;
        uint8_t type = msg[0];
        uint8_t* payload = msg + 1;
        uint32_t payload_len = msg_len - 1;

        switch (type) {
            case 0x10: {  // CMD_LOAD
                bool ok = device.handle_load(payload, payload_len);
                uint8_t ack[] = {0x80, ok ? (uint8_t)0 : (uint8_t)1};
                // send_tcp_ack(tcp_fd, ack, sizeof(ack));  // length-prefixed
                break;
            }
            case 0x11: {  // CMD_START
                if (payload_len >= 8) {
                    int64_t t0;
                    memcpy(&t0, payload, 8);
                    device.handle_start(t0);
                }
                break;
            }
            case 0x12: {  // CMD_JUMP
                if (payload_len >= 12) {
                    int64_t t0;
                    float t_rel;
                    memcpy(&t0, payload, 8);
                    memcpy(&t_rel, payload + 8, 4);
                    device.handle_jump(t0, t_rel);
                }
                break;
            }
            case 0x03: {  // CMD_SYNC_RESULT
                if (payload_len >= 14) {
                    // seq (2) + boot_seq (4) + offset (8)
                    handle_sync_result(payload, payload_len);
                }
                break;
            }
            // Debug commands (ESPSimulated only):
            // case 0x20: device.debug_pause(); break;
            // case 0x22: device.debug_seek(read_f32(payload)); break;
            // etc.
        }

        // Shift remaining data to front
        uint32_t consumed = 4 + msg_len;
        tcp_buf_len -= consumed;
        if (tcp_buf_len > 0)
            memmove(tcp_buf, tcp_buf + consumed, tcp_buf_len);
    }
}
```

### UDP sync probes (device side)

Sync probes use a separate UDP socket. The device listens for SYNC_REQ and replies with SYNC_RESP on the same socket:

```cpp
void poll_udp_sync(int udp_fd) {
    uint8_t buf[64];
    struct sockaddr_in sender;
    socklen_t sender_len = sizeof(sender);

    int n = recvfrom(udp_fd, buf, sizeof(buf), MSG_DONTWAIT,
                     (struct sockaddr*)&sender, &sender_len);
    if (n < 1) return;

    if (buf[0] == 0x01 && n >= 15) {  // SYNC_REQ
        int64_t t2 = now_mono();

        uint8_t resp[31];
        resp[0] = 0x02;  // SYNC_RESP
        memcpy(resp + 1, buf + 1, 14);  // echo seq + boot_seq + t1
        memcpy(resp + 15, &t2, 8);
        int64_t t3 = now_mono();
        memcpy(resp + 23, &t3, 8);

        sendto(udp_fd, resp, 31, 0,
               (struct sockaddr*)&sender, sender_len);
    }
}
```

### Device main loop (combined)

```cpp
void loop() {              // Arduino (ESPDevice)
    poll_tcp_commands(tcp_fd, device);
    poll_udp_sync(udp_fd);
    device.tick_once();
}
```

```cpp
while (running) {          // Desktop (ESPSimulated)
    poll_tcp_commands(tcp_fd, device);
    poll_udp_sync(udp_fd);
    device.tick_once();
    pace_loop();           // sleep_until next_tick, overrun detection
}
```

### Controller side (sending commands)

```python
# Python controller — sending a blob over TCP
def send_load(conn: socket.socket, blob: bytes):
    msg = struct.pack('<I', 1 + len(blob))  # length prefix
    msg += b'\x10'                           # CMD_LOAD
    msg += blob
    conn.sendall(msg)

    # Read ACK
    ack_hdr = conn.recv(4)                   # length prefix
    ack_len = struct.unpack('<I', ack_hdr)[0]
    ack = conn.recv(ack_len)
    assert ack[0] == 0x80                    # CMD_ACK
    return ack[1] == 0                       # status: 0 = ok

def send_start(conn: socket.socket, t0: int):
    msg = struct.pack('<IB', 9, 0x11)        # length=9, CMD_START
    msg += struct.pack('<q', t0)             # t0 as int64
    conn.sendall(msg)

def send_jump(conn: socket.socket, t0: int, t_rel: float):
    msg = struct.pack('<IBqf', 13, 0x12, t0, t_rel)  # length=13, CMD_JUMP, t0, t_rel
    conn.sendall(msg)
```

---

## The PlaybackDevice base class

### Responsibilities

- Owns the Engine, Strip, Program, and rgb buffer lifecycle
- Provides `handle_load()` / `handle_start()` / `handle_jump()` for command processing
- Provides `tick_once()` — the shared per-iteration logic
- Manages device state (IDLE -> LOADED -> PLAYING -> PAUSED -> ENDED)
- Defines virtual methods for platform-specific behavior

### State machine

```
       LOAD              START             tick returns false
  IDLE -----> LOADED --------> PLAYING ----------> ENDED
    ^          ^                |   ^               |
    |          | LOAD           |   | RESUME        | LOAD
    |          |----------------|   |               |
    |          |                |   |-------+       |
    |          |           PAUSE|           |       |
    |          |                v           |       |
    |          |             PAUSED --------+       |
    |          |                |                   |
    |          | LOAD           | LOAD              |
    +----------+----------------+-------------------+
```

A new LOAD at any point tears down the current program and replaces it. This is how the base station transitions between songs or back to ambient mode. PAUSE and RESUME are debug extensions available only on ESPSimulated.

JUMP is valid in PLAYING, PAUSED, and LOADED states. It resets the engine and adjusts the time origin so playback continues (or pauses) at the target time.

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

    // Jump to a reset-safe point. Resets the engine and sets shared time origin.
    // t0 is the shared absolute time (same value sent to all devices, like START).
    // t_rel is the target jump time (used for precise frame rendering when paused).
    // Valid only at compiler-identified safe points (controller enforces this).
    void handle_jump(int64_t t0, float t_rel);

    // Update sync offset (from controller SYNC_RESULT, received over TCP).
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
    int64_t _t0 = 0;              // absolute start time from handle_start (us)
    int64_t _sync_offset = 0;     // controller-provided offset (us), 0 until first SYNC_RESULT
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

#### `handle_jump()`

Lightweight seek to a reset-safe point. The controller sends a shared absolute `t0` (computed the same way as for START) so all devices stay synchronized. `t_rel` is included for precise frame rendering when paused. No replay needed — the target is a time where no event carries prior state.

```cpp
void PlaybackDevice::handle_jump(int64_t t0, float t_rel) {
    if (!_engine) return;

    // Clamp to valid range
    float dur = duration();
    if (t_rel < 0.0f) t_rel = 0.0f;
    if (t_rel > dur) t_rel = dur;

    _engine->reset();
    _t0 = t0;  // shared absolute time — same on all devices (like START)

    if (_state == PLAYING) {
        // Continue playing from jump point
        // Next tick_once() computes t_rel from the shared _t0
    } else if (_state == PAUSED || _state == LOADED || _state == ENDED) {
        // Render one frame at the exact target, then pause
        _engine->tick(t_rel);
        output_frame();
        _state = PAUSED;
    }
}
```

**Why `t0` must be shared:** If each device derived `_t0` from its own local receipt time (`now_mono() + offset - t_rel * 1e6`), TCP delivery jitter would cause permanent clock skew between devices. The shared `t0` avoids this — same mechanism as CMD_START.

Because jump targets are reset-safe (no event is active at that instant), `reset()` + starting from `t_rel` produces correct output. The engine's cursor will advance to the first event at or after `t_rel` on each layer.

#### `tick_once()`

The shared per-iteration logic. Called by each platform's loop.

```cpp
bool PlaybackDevice::tick_once() {
    if (_state != PLAYING)
        return _state != IDLE;  // LOADED, PAUSED, or ENDED: still "active" but not ticking

    int64_t now = now_mono() + _sync_offset;    // virtual mono + controller offset
    float t_rel = (float)(now - _t0) / 1e6f;  // us -> seconds

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

Runs on ESP32 under Arduino framework. Uses `esp_timer_get_time()` (monotonic us) + controller-provided sync offset for time, FastLED for output. Receives commands over TCP, sync probes over UDP, sends telemetry over UDP.

```cpp
class ESPDevice : public PlaybackDevice {
public:
    ESPDevice(uint16_t strip_length)
        : PlaybackDevice(strip_length, /*gamma_enabled=*/true) {
        // Hardware init: FastLED, WiFi, TCP server, UDP socket
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
int tcp_fd, udp_fd;

void setup() {
    // WiFi init (no NTP needed — controller handles sync)
    // FastLED.addLeds<WS2811, PIN, GRB>((CRGB*)device.rgb_buf(), NUM_LEDS);
    // tcp_fd = start_tcp_server(TCP_PORT);
    // udp_fd = bind_udp(UDP_PORT);
}

void loop() {
    poll_tcp_commands(tcp_fd, device);   // LOAD, START, JUMP, SYNC_RESULT
    poll_udp_sync(udp_fd);              // SYNC_REQ -> SYNC_RESP
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
        _state = PAUSED;  // LOADED -> PAUSED on first step
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
int tcp_fd, udp_fd;

int main() {
    // Parse args, connect TCP to controller, bind UDP
    // tcp_fd = connect_tcp(controller_addr, TCP_PORT);
    // udp_fd = bind_udp(UDP_PORT);

    auto next_tick = steady_clock::now();
    auto dt = chrono::milliseconds(20);  // 50Hz

    while (running) {
        poll_tcp_commands(tcp_fd, device);   // LOAD, START, JUMP, SYNC_RESULT, debug
        poll_udp_sync(udp_fd);              // SYNC_REQ -> SYNC_RESP
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
t_rel = (now_epoch_approx - t0) / 1e6        (microseconds -> float seconds)
```

- **ESP:** `now_mono` = `esp_timer_get_time()` (microsecond monotonic). No epoch knowledge needed — the controller provides `sync_offset`.
- **Simulator:** `now_mono` = `steady_clock` (monotonic, immune to wall-clock/NTP jumps on host). For local-only use, `sync_offset = 0` — the controller and simulator share the same clock domain.
- **Controller:** uses its own monotonic clock as the reference. Epoch alignment is irrelevant — what matters is stability and consistency across devices.

All protocol timestamps are **int64_t microseconds** — avoids float byte-order issues and keeps deterministic precision.

### Custom sync protocol (replaces NTP on ESP)

Instead of each ESP running an NTP client, the controller performs a lightweight sync exchange over a dedicated UDP channel. Sync probes use UDP (not the TCP command channel) because RTT measurement requires minimal, predictable latency — TCP's head-of-line blocking and Nagle's algorithm would add jitter that corrupts offset calculations. The computed offset (SYNC_RESULT) is delivered over the TCP command connection since it's a one-shot value, not latency-sensitive.

**Why not NTP:**
- NTPClient on ESP is fragile: `forceUpdate()` blocks up to 1s, `getEpochTime()` loses sub-second precision via integer division, managing the library is unnecessary complexity.
- The controller already talks to every device. A custom protocol is simpler on the ESP side (~15 lines), non-blocking, and gives the controller full visibility into sync quality per device.

#### Sync exchange

```
Controller                           ESP
    |                                 |
    |  SYNC_REQ { seq, t1 }         |
    |-------------------------------->|
    |                                 |  t2 = esp_timer_get_time()
    |  SYNC_RESP { seq, t1, t2, t3 }|  t3 = esp_timer_get_time()
    |<--------------------------------|
    |  t4 = mono_now()               |
    |                                 |
    |  rtt = (t4 - t1) - (t3 - t2)
    |  offset = ((t2 - t1) + (t3 - t4)) / 2
```

- **T1, T4:** controller monotonic timestamps (int64_t us).
- **T2, T3:** ESP monotonic timestamps (int64_t us) — both from `esp_timer_get_time()`, same clock source.
- **seq:** uint16_t sequence number — controller ignores stale/mismatched replies.
- **boot_seq:** uint32_t boot counter — ensures offsets from a pre-reboot timer are not reused.

Packet format (all fields little-endian):

```
SYNC_REQ:    [type: u8 = 0x01] [seq: u16] [boot_seq: u32] [t1: i64]                      -> 15 bytes (UDP)
SYNC_RESP:   [type: u8 = 0x02] [seq: u16] [boot_seq: u32] [t1: i64] [t2: i64] [t3: i64]  -> 31 bytes (UDP)
SYNC_RESULT: [type: u8 = 0x03] [seq: u16] [boot_seq: u32] [offset: i64]                   -> 15 bytes (TCP, length-prefixed)
```

#### ESP implementation (minimal, non-blocking)

The sync probe exchange (SYNC_REQ/SYNC_RESP) runs on UDP for latency accuracy. The computed result (SYNC_RESULT) arrives over TCP with the other commands.

```cpp
int64_t _sync_offset = 0;    // set by controller via SYNC_RESULT (TCP)
uint32_t _boot_seq = 0;     // incremented on each boot
uint16_t _last_sync_seq = 0; // last applied SYNC_RESULT seq

// Called from poll_udp_sync() — UDP path
void handle_sync_req(const uint8_t* pkt, const struct sockaddr_in& sender) {
    int64_t t2 = esp_timer_get_time();

    uint8_t resp[31];
    resp[0] = 0x02;  // SYNC_RESP
    memcpy(resp + 1, pkt + 1, 14);  // echo seq + boot_seq + t1
    memcpy(resp + 15, &t2, 8);
    int64_t t3 = esp_timer_get_time();
    memcpy(resp + 23, &t3, 8);
    sendto(udp_fd, resp, 31, 0, (struct sockaddr*)&sender, sizeof(sender));
}

// Called from poll_tcp_commands() — TCP path
void handle_sync_result(const uint8_t* payload, uint32_t len) {
    if (len < 14) return;

    uint16_t seq;
    uint32_t boot_seq;
    memcpy(&seq, payload, 2);
    memcpy(&boot_seq, payload + 2, 4);

    // Drop stale: wrong boot epoch or old/reordered sequence
    if (boot_seq != _boot_seq || seq < _last_sync_seq)
        return;

    _last_sync_seq = seq;
    memcpy(&_sync_offset, payload + 6, 8);
}

int64_t now_epoch_approx() {
    return esp_timer_get_time() + _sync_offset;
}
```

No NTP library. No blocking. No state machine. The ESP is a passive responder for sync probes — it timestamps and echoes. The controller owns all filtering and correction logic.

#### Controller sync policy

**Startup (before first LOAD):**

1. Send 8 SYNC_REQ rounds at 1-second intervals.
2. For each round, compute RTT and offset.
3. Filter: discard samples where `rtt > rtt_max` or `rtt < 0`.
4. Sort remaining by RTT, keep lowest K (e.g., K=3-4) — low RTT means less asymmetric jitter.
5. Compute median offset of those K candidates.
6. Send SYNC_RESULT to ESP.

**Steady-state (during and between playback):**

Periodic probes at an adaptive interval:

| Condition | Probe interval |
|-----------|---------------|
| Startup calibration | 1s (8 rounds) |
| Steady, good confidence | 15s |
| Poor confidence or high variance | 5-10s |
| Idle (no playback, low priority) | 20-30s |

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
        if abs(delta) < 2000:   # < 2ms in us
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

When the ESP receives a SYNC_RESULT (over TCP) with an updated offset:

- **ESP clock is early** (offset correction makes `t_rel` smaller -> animation was ahead): next `tick_once()` produces a smaller `t_rel` than expected. The engine effectively stalls for one frame (renders the same visual position twice). Invisible at 20ms frame intervals.
- **ESP clock is late** (offset correction makes `t_rel` larger -> animation was behind): next `tick_once()` produces a larger `t_rel` jump. The engine's cursor naturally skips past finished events — this is a single `tick()` call, no replay needed.

Both directions are handled gracefully by the existing engine design. Corrections filtered through the median window + sustained-move guard are small (a few ms), so the frame-to-frame timing perturbation is imperceptible.

### Simulator clock

ESPSimulated uses `steady_clock` (monotonic) for its tick loop. This is immune to wall-clock jumps from NTP adjustments on the host machine.

For local-only use (no real ESPs): `sync_offset = 0`. The controller and simulator share the same clock domain, so no sync exchange is needed.

For mixed real+simulated setups: the controller can derive a trivial offset for the simulator from the identity relationship (same machine = same monotonic base). Real ESPs go through the full sync protocol.

### Seek and virtual time

Two seek mechanisms exist with different tradeoffs:

**CMD_JUMP (all devices):** Resets the engine and starts ticking from a reset-safe point. O(1) — no replay. Valid only at compiler-identified safe points where no event carries prior state. See "Reset-safe jump points" section below.

**CMD_DEBUG_SEEK (simulator only):** Resets the engine and replays frame-by-frame from t=0 to the target. Correct at any arbitrary time, but cost is proportional to the target time. Used for precise scrubbing during development.

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

## Engine::reset() -- required new method

Needed for seek/step/loop in the simulator and jump-point seek on all devices. The engine's cursor is forward-only; `tick(t)` assumes monotonically increasing time. To seek backward (or forward safely), the engine must return to its initial state.

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

For jump-point seek (CMD_JUMP), replay is not needed — the target time is a reset-safe point where no event carries prior state, so `reset()` alone leaves the engine in the correct state to begin ticking from that point.

---

## Program manifest and identity

### Compiler return type

The compiler produces a **manifest** — not just blobs, but metadata about the compiled program:

```python
# What compile_program() currently returns:
dict[str, bytes]   # strip_name -> blob

# What the controller wraps it into:
{
    "artifact_id": "sha256(...)",      # compiled artifact identity (see below)
    "duration": 612.0,                 # seconds
    "jump_points": [0.0, 12.4, 28.0, 44.5, 58.0],  # reset-safe times (global)
    "strips": {
        "main_left":  { "blob": b"..." },
        "main_right": { "blob": b"..." }
    }
}
```

The blob format and decoder are unchanged. The manifest is controller-level metadata — devices never see it. They receive bare blobs via LOAD as before.

### Artifact caching

The compiled manifest (blobs + jump points + duration) should be cached as a single unit keyed by all inputs that affect the output:

```
artifact_id = sha256(dsl_source + config_hash + compiler_version)
```

All three components matter:
- **DSL source** — different program text produces different blobs and jump points
- **Config hash** — strip lengths and mapping affect compilation (e.g., pixel bounds validation)
- **Compiler version** — changes to layer inference, blob format, or jump-point rules change the output

On load, the controller checks the cache first. Cache hit skips compilation entirely and serves the stored manifest. Cache miss triggers compilation and stores the result.

The cache stores the full artifact — blobs, jump points, duration, strip metadata. No separate caching of individual components. This keeps blobs and metadata consistent by construction.

### Identity model

Three levels of identity track what's loaded and what's happening:

| Identity | What it means | When it changes |
|----------|---------------|-----------------|
| **artifact_id** | Compiled artifact identity — "what exact compiled result is this?" | New source, config change, or compiler version change |
| **session_id** | Active load — "which live run of this artifact?" | New on each LOAD (even reloading the same artifact) |
| **epoch** | Continuity marker — "has playback been interrupted?" | Increments on seek, restart, jump |

**Why session_id matters:** When the controller loads a new program, old frames from the previous program may still be in UDP flight. Without session_id, the browser can't distinguish stale frames from current ones.

**Why epoch matters:** Within a session, seek/jump creates a discontinuity. Frames from before the seek have the old epoch; frames after have the new epoch. The browser drops frames with an epoch lower than the current one.

### Example flow

```
Controller                              Web App / Browser
    |                                        |
    |  load_program("song_abc")              |
    |  session_id = 42                       |
    |                                        |
    |  { "event": "session_start",           |
    |    "session_id": 42,                   |
    |    "artifact_id": "song_abc",           |
    |    "duration": 612.0,                  |
    |    "jump_points": [0.0, 12.4, ...],    |
    |    "strips": [...] }                   |
    |--------------------------------------->|
    |                                        |  browser renders seek bar
    |  play()                                |     with jump markers
    |  epoch = 1                             |
    |                                        |
    |  streamed frame:                       |
    |  { "session_id": 42, "epoch": 1,       |
    |    "strip_id": "main_left",            |
    |    "t_rel": 0.34, "rgb": [...] }       |
    |--------------------------------------->|  browser renders
    |                                        |
    |  ... frames flow ...                   |
    |                                        |
    |  seek(30.0) -> snaps to 28.0           |
    |  epoch = 2                             |
    |                                        |
    |  late frame from epoch 1 arrives       |
    |--------------------------------------->|  browser drops (epoch < 2)
    |                                        |
    |  frame from epoch 2 arrives            |
    |--------------------------------------->|  browser renders
```

### What carries session_id and epoch

Everything streamed from controller to web app / browser:

- **RGB frames** — `{ session_id, epoch, strip_id, t_rel, rgb }`
- **Playback state events** — `{ session_id, epoch, state, t_rel }`
- **Telemetry** — `{ session_id, strip_id, ... }` (epoch optional)

Devices don't know about session_id or epoch. These are controller-level concepts stamped onto outgoing data before forwarding to the web app.

---

## Reset-safe jump points

### Definition

A time `t` is **reset-safe** if the visual output at `t` depends only on events starting at or after `t` — not on any state accumulated before `t`. At a reset-safe point, `engine.reset()` followed by `engine.tick(t)` produces correct output without replaying earlier frames.

In this engine, a time is reset-safe when **no event is active on any layer** at that instant. Concretely: for every layer, either the cursor is between events (previous event ended, next event hasn't started) or before the first event.

### Why this matters

The engine's cursor is forward-only. To seek to an arbitrary time, the simulator replays from t=0 — expensive and impractical on real ESP hardware. But if the target time is a reset-safe point, the engine can simply reset and start ticking from there. Cost is O(1) instead of O(target_time).

### What creates history-dependence

**Stateless animations** (wave, spark, paint) — output is a pure function of `(t - event_start)` and params. No history. But the engine can't "skip into" an active event mid-way through without first creating the animation instance, which happens on the first tick where the event is active.

**Stateful animations** (shift) — snapshots source pixels into a work buffer at construction (`engine.cpp:38-57`). The snapshot depends on whatever the source layer rendered before the shift was created. Jumping into the middle of a shift with a blank source buffer produces wrong output.

**Source layer dependencies** — a shift on layer 2 with `source_layer=0` captures layer 0's buffer state at the moment the shift event activates. If layer 0 had a wave running before that, the wave's visual state at that instant is baked into the shift. Skipping past the wave means the shift gets an empty source buffer.

The conservative rule avoids all of these: if no event is active anywhere, there's no state to miss.

### Compiler analysis

The compiler already has the complete event timeline after time resolution and layer inference. Finding reset-safe points is a straightforward interval sweep:

```python
def _find_jump_points(layers: list[dict], duration: float) -> list[float]:
    """Find times where no event is active on any layer."""
    # Collect all active intervals across all layers
    intervals = []
    for layer in layers:
        for e in layer["events"]:
            intervals.append((e["at_sec"], e["at_sec"] + e["duration_sec"]))

    if not intervals:
        return [0.0]

    # Sort by start time, sweep for gaps
    intervals.sort()
    points = [0.0]  # t=0 is always safe (fresh engine state)
    end = 0.0
    for start, stop in intervals:
        if start > end:
            # Gap found: [end, start) has no active events
            # The safe point is at the start of the gap
            points.append(end)
        end = max(end, stop)
    if end < duration:
        points.append(end)  # gap between last event and program end

    return points
```

This runs once per strip during compilation, after layer inference (step 4) and before blob emission (step 7). It adds negligible cost — one sort and one linear sweep over all events.

### Per-strip vs global jump points

Each strip may have different event timelines, producing different safe points. For synchronized multi-device jumps, the controller computes the **intersection** of all per-strip safe points:

```python
def global_jump_points(per_strip_points: dict[str, list[float]]) -> list[float]:
    """Intersection of per-strip safe points."""
    if not per_strip_points:
        return []
    sets = [set(pts) for pts in per_strip_points.values()]
    common = sets[0]
    for s in sets[1:]:
        common &= s
    return sorted(common)
```

Example:
- Strip A safe at `[0.0, 4.0, 8.0, 12.0]`
- Strip B safe at `[0.0, 8.0, 12.0, 16.0]`
- Global safe points: `[0.0, 8.0, 12.0]`

If the user seeks to 10.0, the controller snaps to the nearest global safe point (either 8.0 or 12.0, depending on snap policy).

### Controller seek logic

```python
def handle_seek(self, requested_t: float):
    if self.mode == "sim":
        # Simulators: arbitrary seek via replay (CMD_DEBUG_SEEK)
        for device in self.devices:
            self.send_debug_seek(device, requested_t)
        target = requested_t
    else:
        # ESPs: snap to nearest safe point (CMD_JUMP)
        target = self.snap_to_jump_point(requested_t)
        # Compute shared t0 so all devices are synchronized
        t0 = self.mono_now() - int(target * 1e6)
        for device in self.devices:
            self.send_jump(device, t0, target)

    self.epoch += 1
    self.notify_web_app(epoch=self.epoch, t_rel=target)

def snap_to_jump_point(self, t: float) -> float:
    """Find the nearest global jump point <= t."""
    best = 0.0
    for jp in self.jump_points:
        if jp <= t:
            best = jp
        else:
            break
    return best
```

### Browser seek bar

The controller sends session metadata (including jump points) to the web app when a program is loaded. The browser renders jump markers on the seek bar:

- **Simulator mode:** allow arbitrary scrubbing, markers shown as visual indicators of safe points
- **Production mode:** restrict seek to jump markers only (click-to-jump)

### Edge case: programs with few or no interior jump points

A program with a single long event spanning the full duration (e.g., one wave from 0 to 300s) has only `[0.0]` as a jump point. This is immediately visible from the metadata — the UI can disable the seek bar or show "no jump points available." In practice, music-synced programs have frequent phrase boundaries with brief blackouts, producing many safe points.

---

## Controller responsibilities

The controller is the long-running authority on the base station. It is a separate process from the web app.

### What the controller does

1. **Loads static config** — reads the base-station config file at startup. Knows every strip, device, IP, mode.

2. **Compiles programs** — receives DSL source (from the web app or CLI), compiles it using the existing Python compiler (`compile_program()`), validates strip lengths against config. Produces a manifest: per-strip blobs + duration + jump points.

3. **Routes blobs to devices** — uses config to map `strip_id -> device_id -> ip`. Sends each blob to the correct device via TCP LOAD. Waits for ACK.

4. **Manages sessions** — assigns `session_id` on each load, tracks `epoch` (incremented on seek/jump/restart). Publishes session state to connected clients.

5. **Sends START with shared T0** — over TCP to all devices. All devices begin playback from the same absolute time, so animations synchronize across strips.

6. **Sends JUMP** — snaps requested seek time to nearest global safe point, sends CMD_JUMP to all devices, increments epoch.

7. **Syncs clocks** — sends SYNC_REQ probes (UDP) to each device, receives SYNC_RESP (UDP), computes filtered offsets, sends SYNC_RESULT (TCP).

8. **Receives telemetry** — health, errors, timing from all devices (UDP).

9. **Receives RGB frames from simulators** — stamps with session_id, epoch, strip_id, forwards to web app (which relays to browser via WebSocket).

10. **Exposes a control/event API** — the web app connects to this API to send commands (load, play, pause, seek) and receive events (session start, state changes, frames, telemetry).

### What the controller does NOT do

- Does not serve browser assets (that's the web app)
- Does not speak WebSocket to browsers (that's the web app)
- Does not manage browser connections or sessions

### Compilation flow

```python
# Controller receives DSL source from web app
def load_program(self, dsl_source: str):
    # 1. Compile against config
    strips_config = self.config["strips"]
    # Set up DSL environment, exec source, call build()
    blobs = compile_dsl(dsl_source, beat=..., duration=...)
    # blobs: dict[strip_name, bytes]

    # 2. Validate strip names match config
    for name in blobs:
        if name not in strips_config:
            raise Error(f"strip '{name}' not in config")

    # 3. Compute jump points per strip, then global intersection
    per_strip_jp = {}
    for name, blob in blobs.items():
        per_strip_jp[name] = blob_jump_points[name]  # from compiler
    global_jp = intersect_jump_points(per_strip_jp)

    # 4. Build manifest
    self.session_id += 1
    self.epoch = 0
    self.manifest = {
        "artifact_id": sha256(dsl_source + config_hash + compiler_version),
        "session_id": self.session_id,
        "duration": duration,
        "jump_points": global_jp,
        "strips": {name: {"blob": blob} for name, blob in blobs.items()},
    }

    # 5. Route blobs to devices
    for strip_name, strip_data in self.manifest["strips"].items():
        device = self.device_for_strip(strip_name)  # config lookup
        ok = self.send_load(device.conn, strip_data["blob"])
        if not ok:
            raise Error(f"device {device.id} rejected blob for {strip_name}")

    # 6. Notify web app
    self.publish_session_start(self.manifest)
```

---

## Web app responsibilities

The web app is a separate process. It serves browser assets and relays between the controller and the browser.

### What the web app does

1. **Serves browser assets** — HTML, JS, CSS for the visualization UI
2. **Opens a long-lived connection to the controller** — receives session events, frames, telemetry
3. **Translates browser commands to controller commands** — browser sends `{"cmd": "seek", "t": 30.0}`, web app forwards to controller API
4. **Forwards RGB frames and events to browser** — via WebSocket
5. **Exposes metadata to browser** — strip layout, pixel positions (from config, via controller)

### What the web app does NOT do

- Does not compile programs
- Does not know device IPs
- Does not manage clock sync
- Does not track playback state (the controller is authoritative)

---

## Data flow: end-to-end example

A wave animation on 10 pixels, controller + one ESPSimulated, browser display.

### 1. Startup

```
Web App                   Controller                     ESPSimulated
   |                         |                              |
   | load("song_abc.py")     |                              |
   |------------------------>|                              |
   |                         |  compile -> manifest          |
   |                         |  session_id = 42              |
   |                         |                              |
   |                         |  TCP: LOAD(blob_bytes)       |
   |                         |----------------------------->|
   |                         |                              |  handle_load():
   |                         |                              |    decode_program()
   |                         |                              |    create Engine
   |                         |                              |    state = LOADED
   |                         |  TCP: ACK(status=0)          |
   |                         |<-----------------------------|
   |                         |                              |
   |  { session_start,       |                              |
   |    session_id: 42,      |                              |
   |    duration: 2.0,       |                              |
   |    jump_points: [0.0],  |                              |
   |    strips: [...] }      |                              |
   |<------------------------|                              |
   |                         |                              |
   |  play()                 |                              |
   |------------------------>|                              |
   |                         |  epoch = 1                   |
   |                         |  TCP: START(t0)              |
   |                         |----------------------------->|
   |                         |                              |  handle_start(t0)
   |                         |                              |  state = PLAYING
```

### 2. Playback (ESPSimulated main loop, autonomous)

```
ESPSimulated loop iteration:
   |
   |  now = now_mono() + _sync_offset
   |  t_rel = (now - _t0) / 1e6 -> 0.3
   |
   |  engine.tick(0.3)
   |    -> AnimWave::render() fills layer buffer
   |    -> Compositor blends into rgb_buf (no gamma)
   |    -> rgb_buf = [0,1,5, 0,4,97, 0,1,5, ...]
   |
   |  output_frame()
   |    -> UDP: send rgb_buf (30 bytes) + t_rel to controller
   |
   |  sleep until next frame (20ms cadence)
```

### 3. Controller receives and forwards to browser

```
ESPSimulated              Controller                    Browser
   |                         |                            |
   |  UDP: [rgb + t_rel]     |                            |
   |------------------------>|                            |
   |                         |  stamp with identity:      |
   |                         |  { session_id: 42,         |
   |                         |    epoch: 1,               |
   |                         |    strip_id: "main_left",  |
   |                         |    t_rel: 0.3,             |
   |                         |    rgb: [...] }            |
   |                         |--------------------------->|
   |                         |       (WebSocket)          |
   |                         |                            |  render canvas
   |                         |                            |  update clock
```

### 4. Jump (user clicks a jump marker on seek bar)

```
Browser                   Controller                Devices (all)
   |                         |                         |
   | {"cmd":"seek","t":2.5}  |                         |
   |------------------------>|                         |
   |                         |  snap 2.5 -> 2.0        |
   |                         |  (nearest safe point)   |
   |                         |  epoch = 2              |
   |                         |                         |
   |                         |  TCP: CMD_JUMP(t0, 2.0)  |
   |                         |------------------------>|
   |                         |                         |  handle_jump(t0, 2.0):
   |                         |                         |    engine.reset()
   |                         |                         |    _t0 = t0 (shared)
   |                         |                         |    continue playing
   |                         |                         |
   |                         |  UDP: [rgb + t_rel=2.0] |
   |                         |<------------------------|
   |                         |                         |
   |  { session_id: 42,      |                         |
   |    epoch: 2,            |                         |
   |    strip_id: "main_left"|                         |
   |    t_rel: 2.0,          |                         |
   |    rgb: [...] }         |                         |
   |<------------------------|                         |
   |                         |                         |
   |  drop any late epoch=1  |                         |
   |  frames, render epoch=2 |                         |
```

### 5. Simulator arbitrary seek (debug scrubbing)

When in dev mode with simulators, the controller sends CMD_DEBUG_SEEK for arbitrary positions:

```
Browser                   Controller                ESPSimulated
   |                         |                         |
   | {"cmd":"seek","t":7.3}  |                         |
   |------------------------>|                         |
   |                         |  (dev mode, sims)       |
   |                         |  epoch = 3              |
   |                         |  TCP: DEBUG_SEEK(7.3)   |
   |                         |------------------------>|
   |                         |                         |  debug_seek(7.3):
   |                         |                         |    engine.reset()
   |                         |                         |    replay 0 -> 7.3
   |                         |                         |    output_frame()
```

---

## Device mode and debug coordination

Mixed configurations (real ESPs + simulators in the same show) are not supported. Two clean modes, determined by the base-station config:

- **Production mode** (`mode: "esp"` for all strips): all real ESPs. Controller sends LOAD, START, JUMP, and sync. No replay-based debug commands. Seek is restricted to jump points only.
- **Dev mode** (`mode: "sim"` for all strips): all simulators. Full debug controls (pause/seek/step/jump). Browser supports both arbitrary scrubbing (via CMD_DEBUG_SEEK) and jump-point navigation.

Both modes support CMD_JUMP — it works on any device because it only targets reset-safe points where `reset()` + forward tick is correct.

When the controller sends a debug command (e.g., seek), it sends it to all simulators via their TCP connections without waiting for acknowledgment (fire-and-forget). Each simulator independently resets, replays, and sends its RGB frame. The browser may receive frames from different simulators a few milliseconds apart — at worst a single-frame glitch during a debug operation, invisible in practice.

---

## Open issues / undecided

### 1. Controller <-> Web app protocol

The controller exposes a control/event API to the web app. The exact protocol is not yet decided:
- JSON over TCP?
- HTTP + WebSocket?
- ZMQ?

The web app needs to: send commands (load, play, pause, seek), receive events (session start, state changes), receive streamed RGB frames. A WebSocket-like bidirectional channel is the natural fit, but whether the controller speaks WebSocket directly or uses a simpler TCP protocol with the web app adapting to WebSocket is open.

### 2. Program looping

When a program ends (`tick()` returns false), what happens?
- The device transitions to ENDED state and goes dark.
- The controller can send a new LOAD to restart, or CMD_JUMP to t=0.0 (which is always a safe point).
- For the simulator, auto-loop is useful for preview. For production, the controller decides.

Should looping be a device-level setting (passed with LOAD or START) or a controller-level concern? Mechanically, CMD_JUMP to 0.0 implements loop, but the controller must detect program end (via telemetry) and react. **Not yet decided.**
