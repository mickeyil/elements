# PlaybackDevice — Shared Device Abstraction

> **Status: Design document.** Not yet implemented. Describes planned architecture for the PlaybackDevice hierarchy, transport split, custom clock sync protocol, controller/web-app separation, session identity model, reset-safe intervals, and simulator debug extensions. Prerequisites `Engine::reset()` and Compositor gamma control are implemented in `src/engine.cpp` and `src/compositor.cpp`.

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

| Field | Used by | Scope | Purpose |
|-------|---------|-------|---------|
| `strip_id` (key) | Controller, compiler | Compile | Logical name — matches DSL `strip("main_left", ...)` |
| `length` | Controller, compiler | Compile | Validated against DSL-declared strip length at compile time |
| `device_id` | Controller | Runtime | Human-readable device name for logs/UI |
| `ip` | Controller | Runtime | Where to open TCP/UDP connections |
| `mode` | Controller | Runtime | `"sim"` or `"esp"` — determines seek behavior and debug capabilities |
| `simulation.layout` | Web app, browser | Runtime | Pixel positions for canvas rendering |

The **Compile** fields (`strip_id`, `length`) affect compiled output and are included in `config_hash` for artifact caching. **Runtime** fields are deployment and display concerns — changing them does not invalidate the artifact cache.

### Relationship to the DSL

The DSL declares strip names and lengths: `strip("main_left", length=150)`. The config maps those names to physical devices. The controller validates at compile time that DSL-declared lengths match config-declared lengths. If they disagree, compilation fails with a clear error.

### Relationship to device discovery

The config replaces dynamic discovery. The controller reads it at startup and knows every device's address, role, and capabilities. ESPSimulated processes are started separately and listen on the configured IPs/ports. No multicast, no announcements — the config is the truth.

---

## Transport architecture

Three channels per device, split by requirements:

| Channel | Direction | Purpose | Why this transport |
|---------|-----------|---------|-------------------|
| **TCP** | controller → device | LOAD, START, JUMP, PAUSE, RESUME, STOP, SYNC_RESULT, debug commands | Reliable delivery, arbitrary payload size (blobs can exceed UDP MTU) |
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
CMD_LOAD:         type = 0x10, payload = [gen: u16] [blob: variable]    -> 3+ bytes total
CMD_START:        type = 0x11, payload = [t0: i64]                     -> 9 bytes total
CMD_JUMP:         type = 0x12, payload = [t0: i64] [t_rel: f32] [gen: u16] -> 15 bytes total
CMD_PAUSE:        type = 0x13, no payload                              -> 1 byte total
CMD_RESUME:       type = 0x14, payload = [t0: i64]                     -> 9 bytes total
CMD_STOP:         type = 0x15, no payload                              -> 1 byte total
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

**CMD_JUMP vs CMD_DEBUG_SEEK:** CMD_JUMP is a lightweight seek available on all devices. It carries a shared absolute `t0` (like CMD_START) so all devices stay synchronized, `t_rel` for precise frame rendering when paused, and a `gen` value the device echoes on outbound UDP so the controller can drop stale in-flight frames. Valid only within reset-safe intervals (see "Reset-safe jump points" below). CMD_DEBUG_SEEK is simulator-only: it replays from t=0 to the target, producing correct output at any arbitrary time.

**CMD_PAUSE vs CMD_RESUME:** CMD_PAUSE stops the device from advancing. The device keeps displaying the last rendered frame and reports its last `t_rel` via telemetry. CMD_RESUME(t0) sets a new shared time origin and transitions to PLAYING **without resetting the engine** — all cursor positions, active animation instances, and stateful buffers are preserved. This is fundamentally different from CMD_JUMP, which resets the engine and is only valid within safe intervals. Resume works at any time because the engine state is already correct.

**Generation counter (`gen`):** LOAD and JUMP carry a `gen` value (u16) that the device stores and includes on every outbound UDP frame. The controller increments `gen` on each LOAD and JUMP, and drops any incoming UDP frame whose `gen` doesn't match the current expected value. This prevents stale in-flight frames — emitted before the device processed the command — from being forwarded to the browser with the wrong epoch. See "Session identity and frame filtering" below.

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
                if (payload_len < 2) break;
                uint16_t gen;
                memcpy(&gen, payload, 2);
                bool ok = device.handle_load(payload + 2, payload_len - 2, gen);
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
                if (payload_len >= 14) {
                    int64_t t0;
                    float t_rel;
                    uint16_t gen;
                    memcpy(&t0, payload, 8);
                    memcpy(&t_rel, payload + 8, 4);
                    memcpy(&gen, payload + 12, 2);
                    device.handle_jump(t0, t_rel, gen);
                }
                break;
            }
            case 0x13: {  // CMD_PAUSE
                device.handle_pause();
                break;
            }
            case 0x14: {  // CMD_RESUME
                if (payload_len >= 8) {
                    int64_t t0;
                    memcpy(&t0, payload, 8);
                    device.handle_resume(t0);
                }
                break;
            }
            case 0x15: {  // CMD_STOP
                device.handle_stop();
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
def send_load(conn: socket.socket, blob: bytes, gen: int):
    msg = struct.pack('<I', 1 + 2 + len(blob))  # length prefix
    msg += b'\x10'                               # CMD_LOAD
    msg += struct.pack('<H', gen)                # generation counter
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

def send_jump(conn: socket.socket, t0: int, t_rel: float, gen: int):
    msg = struct.pack('<IBqfH', 15, 0x12, t0, t_rel, gen)  # length=15, CMD_JUMP, t0, t_rel, gen
    conn.sendall(msg)

def send_stop(conn: socket.socket):
    msg = struct.pack('<IB', 1, 0x15)  # length=1, CMD_STOP
    conn.sendall(msg)
```

---

## The PlaybackDevice base class

### Responsibilities

- Owns the Engine, Strip, Program, and rgb buffer lifecycle
- Provides `handle_load()` / `handle_start()` / `handle_jump()` / `handle_pause()` / `handle_resume()` / `handle_stop()` for command processing
- Provides `tick_once()` — the shared per-iteration logic
- Manages device state (IDLE -> LOADED -> PLAYING -> PAUSED -> ENDED)
- Defines virtual methods for platform-specific behavior

### State machine

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

A new LOAD at any point tears down the current program and replaces it. This is how the base station transitions between songs or back to ambient mode. PAUSE and RESUME are production commands available on all devices — they preserve engine state and coordinate with audio (see `handle_pause()` / `handle_resume()`). STOP clears output to black, resets to t=0, and transitions to LOADED — the program remains loaded and ready to play again. Debug extensions (DEBUG_PAUSE, DEBUG_SEEK, etc.) are separate and simulator-only.

JUMP is valid in PLAYING, PAUSED, and LOADED states. It resets the engine and adjusts the time origin so playback continues (or pauses) at the target time.

### Class outline

```cpp
class PlaybackDevice {
public:
    PlaybackDevice(uint16_t strip_length, bool gamma_enabled = true);
    virtual ~PlaybackDevice();

    // --- Command handlers (called by subclass when a command arrives) ---

    // Load a new program. Tears down any existing program first.
    // gen is the controller-supplied generation counter, echoed on outbound UDP.
    // Returns true on success, false on decode error.
    bool handle_load(const uint8_t* blob, size_t blob_len, uint16_t gen);

    // Begin playback. t0 is the absolute start time (int64_t, microseconds).
    // The device computes t_rel = (now_mono() + _sync_offset - t0) / 1e6 each frame.
    void handle_start(int64_t t0);

    // Jump to a reset-safe time. Resets the engine and sets shared time origin.
    // t0 is the shared absolute time (same value sent to all devices, like START).
    // t_rel is the target jump time (used for precise frame rendering when paused).
    // gen is the new generation counter, echoed on outbound UDP.
    // Valid only within compiler-identified safe intervals (controller enforces this).
    void handle_jump(int64_t t0, float t_rel, uint16_t gen);

    // Pause playback. Stops ticking, keeps displaying last frame.
    // Reports last t_rel via telemetry so controller can coordinate resume.
    void handle_pause();

    // Resume playback from paused state. Sets shared time origin WITHOUT resetting
    // the engine — preserves all cursor positions and animation state.
    // t0 is computed by the controller so that the first tick produces the correct t_rel.
    void handle_resume(int64_t t0);

    // Stop playback. Clears output to black, resets engine to t=0,
    // transitions to LOADED. Program remains loaded, ready for play.
    void handle_stop();

    // Update sync offset (from controller SYNC_RESULT, received over TCP).
    void handle_sync_result(int64_t offset);

    // --- Per-iteration logic (called from platform loop) ---

    // Run one tick: advance time, call engine, output frame.
    // Returns true if the device is still active (LOADED, PLAYING, or PAUSED).
    // Returns false if IDLE or ENDED.
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
                                const char* error = nullptr) {}  // default no-op

    // --- Internals ---

    uint16_t _strip_length;
    uint8_t* _rgb_buf;       // owned, allocated in constructor
    Strip* _strip;            // view over _rgb_buf
    Engine* _engine;          // owns Program*, created on handle_load
    State _state = IDLE;
    int64_t _t0 = 0;              // absolute start time from handle_start (us)
    int64_t _sync_offset = 0;     // controller-provided offset (us), 0 until first SYNC_RESULT
    uint16_t _gen = 0;            // generation counter, echoed on outbound UDP frames
    uint32_t _frame_index = 0;    // monotonic frame counter, reset on LOAD/JUMP
    float _paused_t_rel = 0.0f;   // t_rel at pause time, reported to controller
    bool _gamma_enabled;
};
```

`_sync_offset = 0` means uncorrected local monotonic time — safe as a default since playback won't start until after `handle_start()`, and by that point the controller should have completed at least one sync round. If sync hasn't happened yet, playback proceeds with uncorrected local time (drift accumulates but no crash or garbage values).

### Key method implementations

#### `handle_load()`

Tears down any existing program, decodes the new blob, creates Engine+Strip.

```cpp
bool PlaybackDevice::handle_load(const uint8_t* blob, size_t blob_len, uint16_t gen) {
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
    _gen = gen;           // controller-supplied, echoed on outbound UDP
    _frame_index = 0;     // reset frame counter for new program
    _state = LOADED;
    send_telemetry(LOADED, 0.0f);
    return true;
}
```

#### `handle_start()`

Records the absolute start time. Playback begins on the next `tick_once()`. Only valid from LOADED or ENDED — use `handle_resume()` to continue from PAUSED.

```cpp
void PlaybackDevice::handle_start(int64_t t0) {
    if (_state != LOADED && _state != ENDED)
        return;  // ignore if no program loaded or already playing

    if (_state == ENDED)
        _engine->reset();

    _t0 = t0;
    _state = PLAYING;
    send_telemetry(PLAYING, 0.0f);
}
```

#### `handle_jump()`

Lightweight seek to a reset-safe time (within a compiler-identified safe interval). The controller sends a shared absolute `t0` (computed the same way as for START) so all devices stay synchronized. `t_rel` is included for precise frame rendering when paused. No replay needed — the target is a time where no event carries prior state.

```cpp
void PlaybackDevice::handle_jump(int64_t t0, float t_rel, uint16_t gen) {
    if (!_engine) return;

    if (t_rel < 0.0f) t_rel = 0.0f;
    if (t_rel >= _duration) return;  // no-op — jumping to/past end is nonsensical

    DeviceState prev = _state;
    _engine->reset();
    _t0 = t0;           // shared absolute time — same on all devices (like START)
    _gen = gen;          // new generation — stale in-flight frames carry the old gen
    _frame_index = 0;    // reset frame counter for new playback segment

    if (prev == PLAYING) {
        // Continue playing from jump target
        // Next tick_once() computes t_rel from the shared _t0
    } else {
        // LOADED, PAUSED, ENDED → render one frame at the exact target, then pause
        _engine->tick(t_rel);
        output_frame();
        _frame_index++;
        _paused_t_rel = t_rel;
        _state = PAUSED;
    }
}
```

**Why `t0` must be shared:** If each device derived `_t0` from its own local receipt time (`now_mono() + offset - t_rel * 1e6`), TCP delivery jitter would cause permanent clock skew between devices. The shared `t0` avoids this — same mechanism as CMD_START.

Because jump targets are reset-safe (no event is active at that instant), `reset()` + starting from `t_rel` produces correct output. The engine's cursor will advance to the first event at or after `t_rel` on each layer.

**Implementation note:** `handle_start()` and `handle_jump()` share the core "set timebase + reset engine" logic. The implementation should extract a common helper (e.g. `apply_timebase(t0, gen)`) to avoid duplicating the timing math, while keeping the two public handlers separate for their distinct state transitions and preconditions.

#### `handle_pause()`

Stops the device from advancing. The last rendered frame stays on the LEDs. Reports the paused `t_rel` via telemetry so the controller can coordinate a synchronized resume across all devices.

```cpp
void PlaybackDevice::handle_pause() {
    if (_state != PLAYING) return;

    _paused_t_rel = current_t_rel();
    _state = PAUSED;
    send_telemetry(PAUSED, _paused_t_rel);
}
```

#### `handle_resume()`

Resumes playback from the paused state. Sets a new shared time origin **without resetting the engine** — all cursor positions, active animation instances, and stateful buffers are preserved. The controller computes `t0` so that the first tick after resume produces the correct `t_rel`.

```cpp
void PlaybackDevice::handle_resume(int64_t t0) {
    if (_state != PAUSED) return;

    _t0 = t0;    // shared absolute time — first tick lands at the right t_rel
    _state = PLAYING;
    // No reset — engine state is preserved from before the pause
    send_telemetry(PLAYING, _paused_t_rel);
}
```

**Why RESUME doesn't reset:** The engine may be mid-event with accumulated state (e.g., a shift animation that has been scrolling pixels). Resetting would destroy that state, and the paused time is almost certainly not in a safe interval. RESUME simply continues ticking from where the engine left off. The small time gap between pause and resume (typically <100ms of clock difference) is handled in a single `tick()` call — the engine advances cursors and renders at the new time without replaying intermediate frames.

#### `handle_stop()`

Stops playback, clears the LEDs to black, resets the engine to t=0, and transitions to LOADED. The program remains loaded and ready for a fresh `play` (which sends START).

```cpp
void PlaybackDevice::handle_stop() {
    if (_state == IDLE) return;

    _engine->reset();
    memset(_rgb_buf, 0, _strip_length * 3);  // black
    output_frame();
    _frame_index = 0;
    _state = LOADED;
    send_telemetry(LOADED, 0.0f);
}
```

#### `tick_once()`

The shared per-iteration logic. Called by each platform's loop.

```cpp
bool PlaybackDevice::tick_once() {
    if (_state == IDLE || _state == ENDED)
        return false;
    if (_state == LOADED || _state == PAUSED)
        return true;  // still "alive" but not advancing

    // PLAYING
    float t_rel = (float)(now_mono() + _sync_offset - _t0) / 1e6f;
    if (t_rel < 0.0f)
        return true;  // scheduled future start — not yet

    if (!_engine->tick(t_rel)) {
        _state = ENDED;
        send_telemetry(ENDED, _duration);
        return false;
    }

    output_frame();
    _frame_index++;
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
    poll_tcp_commands(tcp_fd, device);   // LOAD, START, JUMP, PAUSE, RESUME, STOP, SYNC_RESULT
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
        // Send rgb buffer to controller, tagged with gen and frame_index
        send_rgb_frame(_gen, _frame_index, _rgb_buf, _strip_length * 3);
        _frame_index++;
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
        poll_tcp_commands(tcp_fd, device);   // LOAD, START, JUMP, PAUSE, RESUME, STOP, SYNC_RESULT, debug
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

**CMD_JUMP (all devices):** Resets the engine and starts ticking from a reset-safe time. O(1) — no replay. Valid only within compiler-identified safe intervals where no event carries prior state. See "Reset-safe jump points" section below.

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

For jump seek (CMD_JUMP), replay is not needed — the target time falls within a safe interval where no event carries prior state, so `reset()` alone leaves the engine in the correct state to begin ticking from that time.

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
    "safe_intervals": [(0.0, 0.0), (12.4, 13.0), (28.0, 29.5), (44.5, 46.0), (58.0, 60.0)],  # reset-safe intervals (global)
    "strips": [                              # ordered list — defines canonical strip order
        { "name": "main_left",  "length": 150, "blob": b"..." },
        { "name": "main_right", "length": 150, "blob": b"..." }
    ]
}
```

The blob format and decoder are unchanged. The manifest is controller-level metadata — devices never see it. They receive bare blobs via LOAD as before.

### Artifact caching

The compiled manifest (blobs + safe intervals + duration) should be cached as a single unit keyed by **all compile inputs** — not just source text:

```
artifact_id = sha256(dsl_source + beat + duration + config_hash + compiler_version)
```

All components matter:
- **DSL source** — different program text produces different blobs and safe intervals
- **beat** — beat duration in seconds; all beat-relative timings resolve differently at different tempos (same DSL at BPM=120 vs BPM=140 produces different blobs)
- **duration** — total program length; affects event clipping and safe interval boundaries
- **Config hash** — hashes only the **compile-relevant topology**: `{strip_id: length}` pairs. Deployment details (`ip`, `device_id`, `mode`) and simulation layout are excluded — changing an IP address or switching a device between sim/esp mode must not invalidate the cache, since compilation output is identical
- **Compiler version** — changes to layer inference, blob format, or safe-interval rules change the output

The key principle: `artifact_id` must cover every input to `compile_program(strips, events, beat, duration)`. If any input changes, the output changes, and the cache must miss. `beat` and `duration` are runtime arguments to the compiler (passed via `build(beat=..., duration=...)`), not embedded in the DSL source text, so they must be included explicitly.

On load, the controller checks the cache first. Cache hit skips compilation entirely and serves the stored manifest. Cache miss triggers compilation and stores the result.

The cache stores the full artifact — blobs, safe intervals, duration, strip metadata. No separate caching of individual components. This keeps blobs and metadata consistent by construction.

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
    |    "safe_intervals": [...],             |
    |    "strips": [                         |
    |      {"name":"main_left","length":150},|
    |      {"name":"main_right","length":150}|
    |    ] }                                 |
    |--------------------------------------->|
    |                                        |  browser renders seek bar
    |                                        |  strips[] defines order and
    |                                        |  lengths for program frames
    |  play()                                |     with safe interval markers
    |  epoch = 1                             |
    |                                        |
    |  program frame (binary):               |
    |  [frame_index=10] [t_rel=0.34]         |
    |  [rgb_strip_0] [rgb_strip_1]           |
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

### Generation counter and frame filtering

Devices don't know about session_id or epoch — those are controller-level concepts. But the controller can't simply stamp its current epoch onto incoming UDP frames, because stale frames emitted before a LOAD or JUMP may arrive after the controller has already advanced its epoch. Those stale frames would be incorrectly stamped with the new epoch.

To solve this, LOAD and JUMP commands carry a **generation counter** (`gen`, u16) that the device stores and echoes on every outbound UDP frame.

**When the device starts using the new gen:** synchronously in the command handler, before the next `tick_once()` / `output_frame()` cycle. For `handle_jump()`, this is immediate. For `handle_load()`, `_gen` is set only after successful decode — on decode failure the device goes IDLE and never emits RGB frames, so there is nothing to filter. The first frame emitted after a successful command carries the new gen; any frames already in the UDP pipeline carry the old gen. This is the invariant that makes controller-side filtering work.

**Telemetry is not gen-filtered.** Gen filtering applies to RGB frames forwarded to the browser. Telemetry (health, errors, decode failures) is always accepted by the controller regardless of gen — otherwise the controller would never learn about a failed LOAD.

```
Device outbound UDP frame:
[gen: u16] [frame_index: u32] [t_rel: f32] [rgb: bytes...]
```

The controller increments `gen` on each LOAD and JUMP, and tracks the expected `gen` per device. When a UDP frame arrives, the controller filters by gen and assembles per-strip frames into a program-level frame before forwarding:

```python
def on_device_frame(self, device_id, frame):
    if frame.gen != self.expected_gen[device_id]:
        return  # stale frame from before LOAD/JUMP — drop

    strip_index = self.strip_index_for_device(device_id)
    self.assemble_program_frame(frame.frame_index, strip_index, frame.t_rel, frame.rgb)

def assemble_program_frame(self, frame_index, strip_index, t_rel, rgb):
    """Collect per-strip frames and emit a complete program frame."""
    bucket = self.pending_frames.setdefault(frame_index, {
        "t_rel": t_rel,
        "strips": {},
        "deadline": time.monotonic() + self.frame_deadline,
    })
    bucket["strips"][strip_index] = rgb

    if len(bucket["strips"]) == self.strip_count:
        # All strips present — emit immediately
        self.emit_program_frame(frame_index, bucket)
        del self.pending_frames[frame_index]

def emit_program_frame(self, frame_index, bucket):
    """Send assembled program frame to web app over UDS."""
    # Binary payload: [frame_index:u32] [t_rel:f32] [rgb_0][rgb_1]...[rgb_N-1]
    # Strip order and lengths defined in session snapshot
    payload = struct.pack('<If', frame_index, bucket["t_rel"])
    for i in range(self.strip_count):
        payload += bucket["strips"][i]
    self.send_to_web_app(kind=0x02, payload=payload)

# Periodic cleanup: drop incomplete frames past their deadline
def sweep_stale_frames(self):
    now = time.monotonic()
    for fid in list(self.pending_frames):
        if self.pending_frames[fid]["deadline"] < now:
            del self.pending_frames[fid]  # incomplete — drop entire frame
```

**Frame assembly policy: complete-only.** The controller emits a program frame only when all strips for a given `frame_index` are present. If any strip is missing when the deadline expires, the entire frame is dropped. This keeps semantics clean — the browser only sees coherent frames. One slow device causes dropped frames, not stale-filled partial renders.

**Scope:** `gen` filtering applies to LOAD and JUMP only. CMD_DEBUG_SEEK (simulator-only) does not bump `gen`. A stale frame from just before a debug seek could be stamped with the new epoch, but this is at most a single-frame glitch during interactive dev scrubbing — not worth coupling the debug path to the gen protocol.

### What carries session_id and epoch

Session_id and epoch are controller-level metadata, communicated to the web app / browser via JSON events — not embedded in binary program frames. The session_start event establishes context; epoch changes are sent as separate events. The browser tracks the current session_id and epoch and drops any late-arriving data from a previous epoch.

Program frames (binary, kind=0x02) carry only `frame_index` and `t_rel` — they are implicitly within the current session/epoch because they flow on the same ordered UDS connection as the events.

---

## Reset-safe jump points

### Definition

A time `t` is **reset-safe** if the visual output at `t` depends only on events starting at or after `t` — not on any state accumulated before `t`. At a reset-safe time, `engine.reset()` followed by `engine.tick(t)` produces correct output without replaying earlier frames.

In this engine, a time is reset-safe when **no event is active on any layer** at that instant. Concretely: for every layer, either the cursor is between events (previous event ended, next event hasn't started) or before the first event.

Safe times form **intervals**, not discrete points. A gap between events spans a continuous range `[gap_start, gap_end)` where every time within the gap is reset-safe. Modeling these as intervals is essential for correct multi-strip intersection (see "Per-strip vs global safe intervals" below).

### Why this matters

The engine's cursor is forward-only. To seek to an arbitrary time, the simulator replays from t=0 — expensive and impractical on real ESP hardware. But if the target time falls within a safe interval, the engine can simply reset and start ticking from there. Cost is O(1) instead of O(target_time).

### What creates history-dependence

**Stateless animations** (wave, spark, paint) — output is a pure function of `(t - event_start)` and params. No history. But the engine can't "skip into" an active event mid-way through without first creating the animation instance, which happens on the first tick where the event is active.

**Stateful animations** (shift) — snapshots source pixels into a work buffer at construction (`engine.cpp:38-57`). The snapshot depends on whatever the source layer rendered before the shift was created. Jumping into the middle of a shift with a blank source buffer produces wrong output.

**Source layer dependencies** — a shift on layer 2 with `source_layer=0` captures layer 0's buffer state at the moment the shift event activates. If layer 0 had a wave running before that, the wave's visual state at that instant is baked into the shift. Skipping past the wave means the shift gets an empty source buffer.

The conservative rule avoids all of these: if no event is active anywhere, there's no state to miss.

### Compiler analysis

The compiler already has the complete event timeline after time resolution and layer inference. Finding safe intervals is a straightforward interval sweep:

```python
def _find_safe_intervals(layers: list[dict], duration: float) -> list[tuple[float, float]]:
    """Find time intervals where no event is active on any layer."""
    event_intervals = []
    for layer in layers:
        for e in layer["events"]:
            event_intervals.append((e["at_sec"], e["at_sec"] + e["duration_sec"]))

    if not event_intervals:
        return [(0.0, duration)]  # entire program is safe

    event_intervals.sort()

    safe = []
    end = 0.0
    for start, stop in event_intervals:
        if start > end:
            safe.append((end, start))  # gap: no events active in [end, start)
        end = max(end, stop)
    if end < duration:
        safe.append((end, duration))  # gap between last event and program end

    # t=0 is always safe — ensure it's represented
    if not safe or safe[0][0] > 0.0:
        safe.insert(0, (0.0, 0.0))  # degenerate: only t=0 is safe

    return safe
```

This runs once per strip during compilation, after layer inference (step 4) and before blob emission (step 7). It adds negligible cost — one sort and one linear sweep over all events.

### Per-strip vs global safe intervals

Each strip may have different event timelines, producing different safe intervals. For synchronized multi-device jumps, the controller computes the **intersection** of all per-strip safe intervals — only times that are safe on *every* strip are globally safe:

```python
def intersect_safe_intervals(
    per_strip: dict[str, list[tuple[float, float]]]
) -> list[tuple[float, float]]:
    """Intersection of per-strip safe intervals."""
    if not per_strip:
        return []

    strips = list(per_strip.values())
    result = strips[0]
    for other in strips[1:]:
        result = _pairwise_intersect(result, other)
    return result

def _pairwise_intersect(
    a: list[tuple[float, float]], b: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    """Intersect two sorted interval lists."""
    result = []
    i, j = 0, 0
    while i < len(a) and j < len(b):
        lo = max(a[i][0], b[j][0])
        hi = min(a[i][1], b[j][1])
        if lo <= hi:
            result.append((lo, hi))
        # Advance the interval that ends first
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return result
```

Example:
- Strip A safe intervals: `[(0.0, 0.0), (4.0, 5.0), (8.0, 10.0)]`
- Strip B safe intervals: `[(0.0, 0.0), (4.5, 6.0), (8.0, 9.5)]`
- Global safe intervals: `[(0.0, 0.0), (4.5, 5.0), (8.0, 9.5)]`

Note: `(4.0, 5.0) ∩ (4.5, 6.0) = (4.5, 5.0)`. A point-based model would have recorded `4.0` for A and `4.5` for B — set intersection yields nothing, missing this valid window. This is why intervals are necessary.

If the user seeks to 4.7, it falls within `(4.5, 5.0)` — a valid jump target. If they seek to 6.5, the controller snaps to the nearest safe interval.

### Controller seek logic

```python
def handle_seek(self, requested_t: float):
    if self.mode == "sim":
        # Simulators: arbitrary seek via replay (CMD_DEBUG_SEEK)
        for device in self.devices:
            self.send_debug_seek(device, requested_t)
        target = requested_t
    else:
        # Production: snap to a safe time (CMD_JUMP) + audio coordination
        target = self.snap_to_safe_time(requested_t)
        if target is None:
            return  # no safe interval available
        # Compute shared t0 so all devices and audio are synchronized
        t0 = self.mono_now() - int(target * 1e6)
        self.gen += 1
        for device in self.devices:
            self.expected_gen[device.id] = self.gen
            self.send_jump(device, t0, target, self.gen)
        self.audio.seek_and_start_at(target, t0)

    self.epoch += 1
    self.notify_web_app(epoch=self.epoch, t_rel=target)

def snap_to_safe_time(self, t: float) -> float | None:
    """Find a safe jump time for the requested seek position.

    If t falls within a safe interval [lo, hi), use it directly.
    Otherwise, snap to the start of the latest safe interval before t.
    """
    best = None
    for lo, hi in self.safe_intervals:
        if lo <= t < hi:
            return t  # requested time is inside a safe interval
        if hi <= t:
            best = lo  # start of the latest safe interval before t
    return best
```

### Browser seek bar

The controller sends session metadata (including safe intervals) to the web app when a program is loaded. The browser renders safe regions on the seek bar:

- **Simulator mode:** allow arbitrary scrubbing, safe intervals shown as visual highlights
- **Production mode:** restrict seek to safe intervals only (click within a highlighted region to jump there, or click outside to snap to the nearest safe boundary)

### Edge case: programs with few or no interior safe intervals

A program with a single long event spanning the full duration (e.g., one wave from 0 to 300s) has only `[(0.0, 0.0)]` as a safe interval — the degenerate t=0-only case. This is immediately visible from the metadata — the UI can disable the seek bar or show "no safe jump regions available." In practice, music-synced programs have frequent phrase boundaries with brief blackouts, producing many safe intervals.

---

## Controller responsibilities

The controller is the long-running authority on the base station. It is a separate process from the web app.

### What the controller does

1. **Loads static config** — reads the base-station config file at startup. Knows every strip, device, IP, mode.

2. **Compiles programs** — receives DSL source (from the web app or CLI), compiles it using the existing Python compiler (`compile_program()`), validates strip lengths against config. Produces a manifest: per-strip blobs + duration + safe intervals.

3. **Routes blobs to devices** — uses config to map `strip_id -> device_id -> ip`. Sends each blob to the correct device via TCP LOAD. Waits for ACK.

4. **Manages sessions** — assigns `session_id` on each load, tracks `epoch` (incremented on seek/jump/restart). Publishes session state to connected clients.

5. **Sends START with shared T0** — over TCP to all devices. All devices begin playback from the same absolute time, so animations synchronize across strips.

6. **Sends JUMP** — snaps requested seek time to nearest global safe interval, sends CMD_JUMP to all devices, increments epoch.

7. **Syncs clocks** — sends SYNC_REQ probes (UDP) to each device, receives SYNC_RESP (UDP), computes filtered offsets, sends SYNC_RESULT (TCP).

8. **Receives telemetry** — health, errors, timing from all devices (UDP).

9. **Assembles program frames** — receives per-strip RGB frames from simulators, filters by `gen` (drops stale), groups by `frame_index`, emits complete multi-strip program frames to the web app over UDS. The web app relays to browser via WebSocket.

10. **Exposes a control/event API over UDS** — the web app connects to a Unix Domain Socket to send commands (load, play, pause, seek) and receive events (session start, state changes), program frames, and telemetry. See "Controller ↔ Web app protocol" section.

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

    # 3. Compute safe intervals per strip, then global intersection
    per_strip_si = {}
    for name, blob in blobs.items():
        per_strip_si[name] = blob_safe_intervals[name]  # from compiler
    global_si = intersect_safe_intervals(per_strip_si)

    # 4. Build manifest (strips as ordered list — defines canonical strip order)
    self.session_id += 1
    self.epoch = 0
    strip_order = list(strips_config.keys())  # stable order from config
    self.manifest = {
        "artifact_id": sha256(dsl_source + beat + duration + config_hash + compiler_version),
        "session_id": self.session_id,
        "duration": duration,
        "safe_intervals": global_si,
        "strips": [
            {"name": name, "length": strips_config[name]["length"], "blob": blobs[name]}
            for name in strip_order
        ],
    }
    self.strip_count = len(self.manifest["strips"])

    # 5. Route blobs to devices
    self.gen += 1
    for i, strip_data in enumerate(self.manifest["strips"]):
        device = self.device_for_strip(strip_data["name"])  # config lookup
        self.expected_gen[device.id] = self.gen
        self.strip_index_for[device.id] = i  # maps device → strip index
        ok = self.send_load(device.conn, strip_data["blob"], self.gen)
        if not ok:
            raise Error(f"device {device.id} rejected blob for {strip_data['name']}")

    # 6. Notify web app
    self.publish_session_start(self.manifest)
```

---

## Web app responsibilities

The web app is a separate process. It serves browser assets and relays between the controller and the browser.

### What the web app does

1. **Serves browser assets** — HTML, JS, CSS for the visualization UI
2. **Connects to the controller over UDS** — receives session events, program frames, telemetry on a single ordered connection
3. **Translates browser commands to controller commands** — browser sends `{"type": "cmd", "id": 1, "cmd": "seek", "t": 30.0}`, web app forwards as JSON over UDS (see "Web app ↔ Browser protocol" section)
4. **Relays program frames and events to browser** — via WebSocket (JSON text frames for events, binary frames for program frames). Drops binary frames for slow clients
5. **Exposes metadata to browser** — strip layout, pixel positions (from config, via controller)

### What the web app does NOT do

- Does not compile programs
- Does not know device IPs
- Does not manage clock sync
- Does not track playback state (the controller is authoritative)
- Does not assemble per-device frames (the controller does that)

---

## Controller ↔ Web app protocol

A single **Unix Domain Socket (UDS)** connection carries all traffic between the controller and the web app. One connection, one ordering domain, one backpressure story.

**Exactly one web app client.** The controller accepts one UDS connection from one web app process. Multiple browser clients connect to the web app (via WebSocket), not to the controller — browser fan-out is the web app's concern. This avoids command arbitration and multi-client state fanout on the controller side.

### Why one connection

- **Ordering is guaranteed.** `session_start` → `epoch_changed` → first program frame always arrive in that order. Two sockets would create cross-stream ordering races.
- **Reconnect is simple.** Reconnect → receive snapshot → resume frame flow. No need to synchronize multiple connections.
- **No external dependencies.** No ZMQ, no HTTP server in the controller. Just a Unix socket with length-prefixed records.

### Wire format

```
[length: u32 LE] [kind: u8] [payload...]
```

`length` includes the kind byte but not itself (same convention as the device TCP protocol).

Two record kinds:

#### kind=0x01 — JSON

`payload` is UTF-8 JSON. Every JSON message has a `type` field to distinguish commands, replies, and events.

**Web app → Controller (commands):**

Commands carry an `id` (web-app-assigned, incrementing counter) that the controller echoes in the reply.

```json
{"type": "cmd", "id": 1, "cmd": "load", "source": "...", "beat": 0.5, "duration": 300.0, "loop": true}
{"type": "cmd", "id": 2, "cmd": "play"}
{"type": "cmd", "id": 3, "cmd": "pause"}
{"type": "cmd", "id": 4, "cmd": "seek", "t": 30.0}
{"type": "cmd", "id": 5, "cmd": "stop"}
```

`play` means both fresh start and resume — the controller decides which device command to send based on current state (CMD_START from LOADED/ENDED, CMD_RESUME from PAUSED). The web app does not need to distinguish between them.

**Controller → Web app (replies):**

Replies are **controller-complete** — the reply is sent after the controller has finished all work for the command (compilation, device ACKs, state updates). The web app does not need to track intermediate states or correlate follow-up events.

```json
{"type": "reply", "id": 1, "ok": true, "result": {"session_id": 42}}
{"type": "reply", "id": 4, "ok": true, "result": {"t": 28.0}}
{"type": "reply", "id": 1, "ok": false, "error": "device esp-01 rejected blob"}
```

**Controller → Web app (events):**

Asynchronous state changes and broadcasts — not tied to a specific command.

```json
{"type": "event", "event": "session_start", "session_id": 42, "artifact_id": "...",
 "duration": 612.0, "safe_intervals": [[0.0, 0.0], [12.4, 13.0], ...],
 "strips": [{"name": "main_left", "length": 150}, {"name": "main_right", "length": 150}]}
{"type": "event", "event": "state", "state": "playing", "epoch": 2}
{"type": "event", "event": "loop", "epoch": 3}
{"type": "event", "event": "device_status",
 "device_id": "esp-01", "strip": "main_left", "status": "connected"}
{"type": "event", "event": "device_status",
 "device_id": "esp-01", "strip": "main_left", "status": "disconnected"}
{"type": "event", "event": "error",
 "scope": "device", "device_id": "esp-01", "strip": "main_left", "message": "lost TCP connection"}
{"type": "event", "event": "error",
 "scope": "session", "message": "device esp-01 dropped during playback"}
```

- **`session_start`** — new session loaded, includes all metadata for seek bar and frame slicing
- **`state`** — playback state change (playing, paused, ended), includes current epoch
- **`loop`** — program looped back to t=0, includes new epoch
- **`device_status`** — device lifecycle change (connected/disconnected). State-oriented — UI updates indicators
- **`error`** — async failure. Human-oriented — UI shows notification/log. `scope` is `"device"` (one device, includes `device_id` + `strip`) or `"session"` (program-level). No error codes — message is a human-readable string

The `strips` array in `session_start` defines the **canonical strip order and lengths** for the session. Program frames pack RGB blobs in this exact order with no per-entry headers — the web app and browser use the strip list to slice the payload.

**Command semantics summary:**

| Command | Controller-complete means | Reply result |
|---------|--------------------------|-------------|
| `load` | Compiled (or cache hit), all devices ACKed LOAD, session created | `{"session_id": N}` |
| `play` | State updated; sends START (from LOADED/ENDED) or RESUME (from PAUSED) to all devices + audio | `{}` |
| `pause` | CMD_PAUSE sent to all devices, paused t_rel collected, audio paused, state updated | `{"t_rel": paused_time}` |
| `seek` | Time resolved/snapped, JUMP or DEBUG_SEEK sent, epoch updated | `{"t": snapped_time}` |
| `stop` | CMD_STOP sent to all devices, output cleared to black, state updated | `{}` |

#### kind=0x02 — Program frame

Binary payload, emitted by the controller after assembling all per-strip frames for a given `frame_index`:

```
[frame_index: u32 LE] [t_rel: f32 LE] [rgb_strip_0] [rgb_strip_1] ... [rgb_strip_N-1]
```

- `frame_index` — device-emitted monotonic counter, reset to 0 on LOAD/JUMP
- `t_rel` — playback time in seconds
- `rgb_strip_i` — raw RGB bytes, length = `strips[i].length * 3` (from session snapshot)
- No strip_id, no rgb_len per entry — strip order and sizes are implicit from the session snapshot

### Snapshot

On every connect (first or reconnect), the controller immediately sends a **snapshot** — a single JSON message containing everything the web app needs to start working. No request needed, no handshake. The web app has one bootstrap path: connect → receive snapshot → ready.

```json
{
  "type": "event",
  "event": "snapshot",
  "protocol_version": 1,
  "controller_state": "running",
  "session": {
    "session_id": 42,
    "artifact_id": "sha256(...)",
    "epoch": 2,
    "playback_state": "playing",
    "duration": 612.0,
    "safe_intervals": [[0.0, 0.0], [12.4, 13.0], [28.0, 29.5]],
    "strips": [
      {"name": "main_left", "length": 150},
      {"name": "main_right", "length": 150}
    ]
  },
  "layout": {
    "main_left":  {"x": 0, "y": 0, "dx": 1, "dy": 0},
    "main_right": {"x": 0, "y": 2, "dx": 1, "dy": 0}
  },
  "devices": [
    {"device_id": "esp-01", "strip": "main_left", "mode": "sim", "status": "connected"},
    {"device_id": "esp-02", "strip": "main_right", "mode": "sim", "status": "connected"}
  ]
}
```

If no active session (controller just started, no program loaded yet):

```json
{
  "type": "event",
  "event": "snapshot",
  "protocol_version": 1,
  "controller_state": "running",
  "session": null,
  "layout": { ... },
  "devices": [ ... ]
}
```

**What the snapshot contains:**

| Field | Purpose |
|-------|---------|
| `protocol_version` | Allows the web app to detect incompatible controller versions |
| `controller_state` | Top-level controller health |
| `session` | Active session if any — includes all metadata the browser needs to render the seek bar and receive frames. `null` if no program is loaded |
| `session.strips` | Canonical strip order and lengths — defines how program frame payloads are sliced |
| `layout` | Pixel positions for browser canvas rendering (from static config). Included so the web app doesn't need its own config file |
| `devices` | Per-device status summary — connected/disconnected, mode, which strip each serves |

**Why the controller owns layout:** The static config is the single source of truth for strip topology, device mapping, and simulation layout. Rather than having the web app read a separate config or duplicate data, the controller includes layout in the snapshot. One source, one delivery path.

After the snapshot, the controller sends incremental events (`state`, `session_start`, etc.) and program frames as they occur. The snapshot is never re-sent mid-connection — it's a connect-time-only message.

### Backpressure

The UDS socket is **non-blocking**. The controller never blocks on frame delivery.

- **Program frames (kind=0x02) are lossy.** If a write returns EAGAIN/EWOULDBLOCK, the frame is dropped silently. The web app and browser handle gaps in `frame_index` — they render whatever arrives next. No backpressure propagates into the device coordination path.
- **JSON messages (kind=0x01) are reliable.** Commands, replies, events, and snapshots are infrequent and small. If a JSON write cannot proceed, the web app connection is unhealthy — the controller closes it and waits for reconnect. On reconnect, the web app receives a fresh snapshot and resumes.

---

## Web app ↔ Browser protocol

A single **WebSocket** connection carries all traffic between the web app and the browser. Text frames for JSON, binary frames for program data.

### Message shapes

The browser uses the same `type`/`id` message convention as the UDS protocol. Browser-assigned `id` values are independent of the UDS `id` namespace — the web app maps between them.

**Browser → Web app (commands):**

```json
{"type": "cmd", "id": 1, "cmd": "load", "source": "...", "beat": 0.5, "duration": 300.0, "loop": true}
{"type": "cmd", "id": 2, "cmd": "play"}
{"type": "cmd", "id": 3, "cmd": "pause"}
{"type": "cmd", "id": 4, "cmd": "seek", "t": 30.0}
{"type": "cmd", "id": 5, "cmd": "stop"}
```

Same command vocabulary as the UDS protocol. The web app forwards each browser command to the controller as a UDS command (with its own `id`), then maps the controller's reply back to the browser's `id`.

**Web app → Browser (replies):**

```json
{"type": "reply", "id": 1, "ok": true, "result": {"session_id": 42}}
{"type": "reply", "id": 4, "ok": true, "result": {"t": 28.0}}
{"type": "reply", "id": 1, "ok": false, "error": "device esp-01 rejected blob"}
```

**Web app → Browser (events):**

```json
{"type": "event", "event": "snapshot", "protocol_version": 1, ...}
{"type": "event", "event": "session_start", "session_id": 42, ...}
{"type": "event", "event": "state", "state": "playing", "epoch": 2}
{"type": "event", "event": "loop", "epoch": 3}
{"type": "event", "event": "device_status", "device_id": "esp-01", "strip": "main_left", "status": "connected"}
{"type": "event", "event": "error", "scope": "device", "device_id": "esp-01", "strip": "main_left", "message": "lost TCP connection"}
```

Events are forwarded from the controller with one transformation: **device IPs are stripped**. The browser does not need (and should not see) device network addresses.

**Web app → Browser (program frames):**

Binary WebSocket frames with the same payload as UDS kind=0x02 program frames:

```
[frame_index: u32 LE] [t_rel: f32 LE] [rgb_strip_0] [rgb_strip_1] ...
```

The browser uses the `strips` array from the snapshot to slice the RGB payload, identical to how the web app uses the UDS session snapshot.

### Snapshot on connect

On WebSocket connect, the web app immediately sends a snapshot event — the same snapshot it received from the controller, with IPs stripped. The browser has one bootstrap path: connect → receive snapshot → ready.

If the web app is not yet connected to the controller (or has no snapshot), it sends a snapshot with `controller_state: "connecting"` and `session: null`. Once the UDS connection is established and a controller snapshot arrives, the web app forwards it as a new snapshot event.

### Backpressure

- **Binary frames (program data) are dropped for slow clients.** If a WebSocket send would block or the client's write buffer exceeds a threshold, the web app drops the frame. The browser handles gaps in `frame_index` — it renders whatever arrives. This mirrors the UDS backpressure policy.
- **JSON frames (commands, replies, events) are never dropped.** These are small and infrequent. If a client cannot keep up with JSON messages, the web app closes the WebSocket — the browser reconnects and receives a fresh snapshot.

---

## Data flow: end-to-end example

A wave animation on two strips, controller + two ESPSimulated devices, browser display.

### 1. Startup

```
Web App                   Controller                     ESPSimulated
   |                         |                              |
   | {"type":"cmd","id":1,   |                              |
   |  "cmd":"load",          |                              |
   |  "source":"song_abc.py",|                              |
   |  "beat":0.5,            |                              |
   |  "duration":2.0}        |                              |
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
   |    safe_intervals: ..., |                              |
   |    strips: [            |                              |
   |     {name, length},...  |                              |
   |    ] }                  |                              |
   |<------------------------|                              |
   |                         |                              |
   | {"type":"cmd","id":2,   |                              |
   |  "cmd":"play"}          |                              |
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
   |    -> UDP: [gen + frame_index + t_rel + rgb_buf] to controller
   |    -> _frame_index++
   |
   |  sleep until next frame (20ms cadence)
```

### 3. Controller assembles program frame and forwards to browser

```
ESPSimulated (strip 0)    ESPSimulated (strip 1)    Controller              Browser
   |                         |                         |                      |
   |  UDP: [gen, fi=10,      |                         |                      |
   |   t_rel=0.3, rgb]       |                         |                      |
   |------------------------>|                         |                      |
   |                         |  UDP: [gen, fi=10,      |                      |
   |                         |   t_rel=0.3, rgb]       |                      |
   |                         |------------------------>|                      |
   |                         |                         |  gen ok, fi=10:      |
   |                         |                         |  both strips present |
   |                         |                         |                      |
   |                         |                         |  UDS program frame:  |
   |                         |                         |  [fi=10][t_rel=0.3]  |
   |                         |                         |  [rgb_0][rgb_1]      |
   |                         |                         |--------------------->|
   |                         |                         |     (via web app)    |
   |                         |                         |                      | render canvas
```

### 4. Jump (user clicks within a safe region on seek bar)

```
Browser                   Controller                Devices (all)
   |                         |                         |
   | {"type":"cmd","id":3,   |                         |
   |  "cmd":"seek","t":2.5}  |                         |
   |------------------------>|                         |
   |                         |  2.5 is within safe      |
   |                         |  interval [2.0, 3.0)    |
   |                         |  epoch = 2              |
   |                         |                         |
   |                         |  TCP: CMD_JUMP(t0, 2.5)  |
   |                         |------------------------>|
   |                         |                         |  handle_jump(t0, 2.5):
   |                         |                         |    engine.reset()
   |                         |                         |    _t0 = t0 (shared)
   |                         |                         |    _frame_index = 0
   |                         |                         |    continue playing
   |                         |                         |
   |                         |  UDP: [gen, fi=0,       |
   |                         |   t_rel=2.5, rgb]       |
   |                         |<--- (from all devices)--|
   |                         |                         |
   |                         |  assemble fi=0:         |
   |  program frame           |  all strips present    |
   |  [fi=0][t_rel=2.5]      |                         |
   |  [rgb_0][rgb_1]          |                         |
   |<------------------------|                         |
   |  frames, render epoch=2 |                         |
```

### 5. Simulator arbitrary seek (debug scrubbing)

When in dev mode with simulators, the controller sends CMD_DEBUG_SEEK for arbitrary positions:

```
Browser                   Controller                ESPSimulated
   |                         |                         |
   | {"type":"cmd","id":4,   |                         |
   |  "cmd":"seek","t":7.3}  |                         |
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

## Audio player coordination

The audio player is a separate component on the base station, coordinated by the controller using the same synchronization primitive as device sync: shared absolute `t0` + scheduled start.

### Contract

The audio player must support four operations:

- **`start_at(t0_abs)`** — begin playback from the start, timed to absolute `t0`
- **`seek_and_start_at(t_audio, t0_abs)`** — seek to position `t_audio` in the track, then start/resume playback at absolute `t0`
- **`pause()`** — stop audio playback, retain current position
- **`resume_at(t_audio, t0_abs)`** — resume from `t_audio`, starting at absolute `t0`

All operations using `t0_abs` reference the controller's monotonic clock. The audio player compensates for its own pipeline latency (see `docs/design.md`, "Audio pipeline latency" section).

### How it fits into the controller flows

**START:** The controller computes a shared `t0`, sends `START(t0)` to all devices, and calls `audio.start_at(t0)`. Audio and LEDs begin together.

**PAUSE:** The controller sends `CMD_PAUSE` to all devices, collects their reported `t_rel`, and calls `audio.pause()`. Audio and LEDs stop together.

**RESUME:** The controller picks `t_resume = max(reported t_rel values)`, computes a shared `t0`, sends `CMD_RESUME(t0)` to all devices, and calls `audio.resume_at(t_resume, t0)`. Audio and LEDs resume together.

**JUMP (production seek):** The controller snaps to a safe interval, computes a shared `t0`, sends `JUMP(t0, t_rel, gen)` to all devices, and calls `audio.seek_and_start_at(t_rel, t0)`. Audio and LEDs reposition together.

**JUMP (looping):** Same as seek — `t_rel=0.0`, audio restarts from the beginning at the new `t0`.

### Scope

The audio player component itself is not yet designed. This section documents the required contract so that the controller's seek and start flows are specified end-to-end. The LED side (CMD_JUMP, safe intervals, gen filtering) is fully defined. The audio side depends on this interface being implemented.

Production seek and pause/resume are supported — seek is restricted to safe intervals on the visual side, pause/resume preserves engine state. Both coordinate with audio via the contract above.

---

## Device mode and debug coordination

Mixed configurations (real ESPs + simulators in the same show) are not supported. Two clean modes, determined by the base-station config:

- **Production mode** (`mode: "esp"` for all strips): all real ESPs. Controller sends LOAD, START, JUMP, PAUSE, RESUME, and sync. No replay-based debug commands. Seek is restricted to safe intervals only.
- **Dev mode** (`mode: "sim"` for all strips): all simulators. Full debug controls (pause/seek/step/jump). Browser supports both arbitrary scrubbing (via CMD_DEBUG_SEEK) and jump-point navigation.

Both modes support CMD_JUMP — it works on any device because it only targets times within reset-safe intervals where `reset()` + forward tick is correct.

When the controller sends a debug command (e.g., seek), it sends it to all simulators via their TCP connections without waiting for acknowledgment (fire-and-forget). Each simulator independently resets, replays, and sends its RGB frame. The browser may receive frames from different simulators a few milliseconds apart — at worst a single-frame glitch during a debug operation, invisible in practice.

---

## Open issues / undecided

### ~~1. Controller <-> Web app protocol~~ (decided)

See "Controller ↔ Web app protocol" section below.

### ~~2. Program looping~~ (decided)

**Looping is controller-owned.** The device never auto-loops — it transitions to ENDED and goes dark. The controller decides whether and when to restart.

**Mechanism:** `CMD_JUMP(t0, 0.0, new_gen)`. The program is already loaded, t=0 is always safe, and the gen bump filters stale tail frames from the previous iteration. No full LOAD+START cycle needed.

**How it's enabled:** The `load` command accepts a `loop` flag:
```json
{"type": "cmd", "id": 1, "cmd": "load", "source": "...", "beat": 0.5, "duration": 300.0, "loop": true}
```

**How end-detection works:** When a device's program finishes, it transitions to ENDED and sends telemetry. The controller treats this as a program-level signal — it does not react per-device. Once the controller determines the program has ended (first ENDED telemetry, since all devices share the same t0 and duration), it issues one coordinated JUMP to all devices with a shared t0 and new gen.

**Loop event:** The controller emits a loop event to the web app so the browser can reset its playback position:
```json
{"type": "event", "event": "loop", "epoch": 3}
```

**What this means for the device:** Nothing changes. The device doesn't know about looping. It receives JUMP like any other seek, resets its engine, and continues. The looping policy lives entirely in the controller.
