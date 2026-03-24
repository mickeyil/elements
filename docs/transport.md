# Transport & Clock Sync

> **Status: Mixed.** The TCP command transport (CONFIGURE, LOAD, START, JUMP, PAUSE, RESUME, STOP, DEBUG_SEEK, ACK), UDP frame return path, discovery HELLO flow, and controller-led clock sync protocol (SYNC_REQ/SYNC_RESP/SYNC_RESULT) are implemented. Browser playback/program-control parity and audio integration are still future work.

## Transport architecture

Three active transport paths per device:

| Channel | Direction | Purpose | Why this transport |
|---------|-----------|---------|-------------------|
| **TCP** | bidirectional | Commands (controller → device), ACKs (device → controller), and SYNC_RESULT (controller → device) | Reliable delivery, arbitrary payload size (blobs can exceed UDP MTU) |
| **UDP outbound** | device → controller | RGB frames | Fire-and-forget streaming; dropped frame = client skips one update |
| **UDP discovery / sync** | bidirectional | HELLO packets, discovery rejects, SYNC_REQ, SYNC_RESP | Lightweight presence plus low-latency sync probes on one known UDP port |

Each device listens on one TCP port. The controller maintains a persistent TCP connection to each device. Devices send UDP frames to the controller's `frame_port`. Discovery HELLO packets are broadcast on `discovery_port` (default 6040) — the controller uses these to resolve live `(host, tcp_port)` for known `device_uid` values. Duplicate UIDs from different addresses are rejected via a discovery reject packet.

Clock sync probes reuse that same discovery UDP port. The controller sends `SYNC_REQ` to the device's discovery port, and the device replies with `SYNC_RESP` from the same socket. There is no separate advertised sync port in the current implementation.

`network_sim` keeps sending HELLO packets even after a TCP connection is established, so the controller can rediscover the device after reconnects without requiring a restart.

---

## TCP command protocol

The controller opens a persistent TCP connection to each device. Commands are length-prefixed messages:

```
[length: u32 little-endian] [type: u8] [payload...]
```

`length` includes the type byte but not itself. So a START command (type + 8 bytes of t0) has `length = 9`.

Implemented command types (in controller `wire.py`):

```
CMD_CONFIGURE:    type = 0x04, payload = [device_id: u16] [strip_length: u16] [frame_port: u16] -> 7 bytes total
CMD_LOAD:         type = 0x10, payload = [device_id: u16] [gen: u16] [blob: variable] -> 5+ bytes total
CMD_START:        type = 0x11, payload = [t0: i64]                     -> 9 bytes total
CMD_JUMP:         type = 0x12, payload = [t0: i64] [t_rel: f32] [gen: u16] -> 15 bytes total
CMD_PAUSE:        type = 0x13, no payload                              -> 1 byte total
CMD_RESUME:       type = 0x14, payload = [t0: i64]                     -> 9 bytes total
CMD_STOP:         type = 0x15, no payload                              -> 1 byte total
CMD_DEBUG_SEEK:   type = 0x22, payload = [t_rel: f32]                  -> 5 bytes total
CMD_ACK:          type = 0x80, payload = [status: u8]                  -> 2 bytes total
```

Device-side extensions (implemented in `network_sim` only):

```
CMD_DEBUG_STEP:   type = 0x23, payload = [direction: i8]               -> 2 bytes total
```

Implemented:

```
CMD_SYNC_RESULT:  type = 0x03, payload = [seq: u16] [boot_token: u32] [offset: i64] -> 15 bytes total
```

Design-only (not yet implemented):

```
CMD_DEBUG_PAUSE:  type = 0x20, no payload                              -> 1 byte total
CMD_DEBUG_RESUME: type = 0x21, no payload                              -> 1 byte total
```

The device sends an ACK (status: 0 = ok, 1 = invalid payload/config, 2 = not configured) after CONFIGURE and LOAD. Other commands are fire-and-forget from the controller's perspective — the TCP connection itself provides delivery guarantee.

**CMD_JUMP vs CMD_DEBUG_SEEK:** CMD_JUMP is a lightweight seek available on all devices. It carries a shared absolute `t0` (like CMD_START) so all devices stay synchronized, `t_rel` for precise frame rendering when paused, and a `gen` value the device echoes on outbound UDP so the controller can drop stale in-flight frames. Valid only within reset-safe intervals (see `controller.md`). CMD_DEBUG_SEEK is simulator-only: it replays from t=0 to the target, producing correct output at any arbitrary time.

**CMD_PAUSE vs CMD_RESUME:** CMD_PAUSE stops the device from advancing. The device keeps displaying the last rendered frame and captures its current `t_rel` internally (for correct resume). CMD_RESUME(t0) sets a new shared time origin and transitions to PLAYING **without resetting the engine** — all cursor positions, active animation instances, and stateful buffers are preserved. This is fundamentally different from CMD_JUMP, which resets the engine and is only valid within safe intervals. Resume works at any time because the engine state is already correct.

**Generation counter (`gen`):** LOAD and JUMP carry a `gen` value (u16) that the device stores and includes on every outbound UDP frame. The controller increments `gen` on each LOAD and JUMP, and drops any incoming UDP frame whose `gen` doesn't match the current expected value. This prevents stale in-flight frames from being forwarded to the browser with the wrong epoch.

**Device identity (`device_id`):** The controller assigns `device_id` during CONFIGURE and the device echoes it on every outbound UDP packet. LOAD still carries `device_id` redundantly for compatibility and optional validation. This allows the controller to receive all device frames on a single shared UDP port and demux by `device_id`. This is necessary because multiple simulator instances on the same host share a source IP, making source-address-based demux unreliable.

**Runtime config handshake:** `network_sim` starts with discovery identity and TCP listen state only. After TCP connect, the controller sends CONFIGURE with `device_id`, `strip_length`, and `frame_port`. Until CONFIGURE succeeds, LOAD is rejected with ACK status `2` and all other commands are ignored.

---

## TCP command reading (device side)

The device reads from the TCP socket in its main loop. Commands are length-prefixed, so reading is straightforward and non-blocking:

```cpp
// Resizable read buffer — accumulates partial TCP reads across loop iterations.
// Starts at 32 KiB, grows dynamically for large LOAD payloads.
// Messages exceeding TCP_MSG_MAX are rejected to guard against runaway reads.
std::vector<uint8_t> tcp_buf(32768);
uint32_t tcp_buf_len = 0;

// Called each loop iteration. Non-blocking: reads whatever is available,
// processes complete commands, leaves partial data for next call.
void poll_tcp_commands(int tcp_fd, /* ... */) {
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
            case 0x04: {  // CMD_CONFIGURE
                uint16_t device_id, strip_length, frame_port;
                memcpy(&device_id, payload, 2);
                memcpy(&strip_length, payload + 2, 2);
                memcpy(&frame_port, payload + 4, 2);
                // Creates ESPSimulated with strip_length, stores device_id,
                // programs controller frame_port for UDP output.
                // Rejects reconfigure-after-configure with ACK status 1.
                // Sends ACK status 0 on success.
                break;
            }
            case 0x10: {  // CMD_LOAD
                if (!configured) {
                    send_ack(tcp_fd, 2);  // ACK status 2 = not configured
                    break;
                }
                if (payload_len < 4) break;
                uint16_t device_id, gen;
                memcpy(&device_id, payload, 2);
                memcpy(&gen, payload + 2, 2);
                bool ok = device->handle_load(payload + 4, payload_len - 4, gen);
                send_ack(tcp_fd, ok ? 0 : 1);  // ACK immediately
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
                uint16_t seq;
                uint32_t boot_token;
                int64_t offset;
                memcpy(&seq, payload, 2);
                memcpy(&boot_token, payload + 2, 4);
                memcpy(&offset, payload + 6, 8);
                device->handle_sync_result(offset);
                break;
            }
            // Debug commands (ESPSimulated only, implemented):
            case 0x22: {  // CMD_DEBUG_SEEK
                float t_rel;
                memcpy(&t_rel, payload, 4);
                device->debug_seek(t_rel);
                break;
            }
            case 0x23: {  // CMD_DEBUG_STEP
                int8_t direction = (int8_t)payload[0];
                device->debug_step(direction);
                break;
            }
        }

        // Shift remaining data to front
        uint32_t consumed = 4 + msg_len;
        tcp_buf_len -= consumed;
        if (tcp_buf_len > 0)
            memmove(tcp_buf, tcp_buf + consumed, tcp_buf_len);
    }
}
```

---

## UDP sync probes (device side)

The sync probe exchange is implemented on the same UDP socket used for discovery. Devices inspect incoming packets on the discovery port and respond to `SYNC_REQ` without blocking the main loop.

Sync probes use the discovery UDP socket:

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
        memcpy(resp + 1, buf + 1, 2);   // echo seq
        memcpy(resp + 3, &_boot_token, 4);
        memcpy(resp + 7, buf + 7, 8);   // echo t1
        memcpy(resp + 15, &t2, 8);
        int64_t t3 = now_mono();
        memcpy(resp + 23, &t3, 8);

        sendto(udp_fd, resp, 31, 0,
               (struct sockaddr*)&sender, sender_len);
    }
}
```

---

## Device main loop (combined)

```cpp
void loop() {              // Arduino (ESPDevice, implemented)
    poll_tcp_commands(tcp_fd, device);
    poll_udp_sync(discovery_udp_fd);
    device.tick_once();
}
```

```cpp
while (running) {          // Desktop (network_sim, implemented)
    poll_tcp_commands(tcp_fd, device);
    device->tick_once();
    send_frames();         // drain queued frames, send UDP to controller
    broadcast_hello();     // periodic HELLO on discovery port (even after TCP connect)
    pace_loop();           // sleep_until next_tick, overrun detection
}
```

`network_sim` does not implement active sync probing; simulators are treated as host-locked and use `sync_offset = 0`.

---

## Controller side (sending commands)

```python
# Python controller — sending commands over TCP

def send_configure(conn: socket.socket, device_id: int, strip_length: int, frame_port: int):
    msg = struct.pack('<IB', 7, 0x04)        # length=7, CMD_CONFIGURE
    msg += struct.pack('<HHH', device_id, strip_length, frame_port)
    conn.sendall(msg)
    # Read ACK (status 0 = ok, 1 = already configured)
    return read_ack(conn)

def send_load(conn: socket.socket, device_id: int, gen: int, blob: bytes):
    msg = struct.pack('<I', 1 + 2 + 2 + len(blob))  # length prefix
    msg += b'\x10'                                    # CMD_LOAD
    msg += struct.pack('<HH', device_id, gen)         # device_id + generation counter
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
    msg = struct.pack('<IBqfH', 15, 0x12, t0, t_rel, gen)
    conn.sendall(msg)

def send_pause(conn: socket.socket):
    msg = struct.pack('<IB', 1, 0x13)        # length=1, CMD_PAUSE
    conn.sendall(msg)

def send_resume(conn: socket.socket, t0: int):
    msg = struct.pack('<IB', 9, 0x14)        # length=9, CMD_RESUME
    msg += struct.pack('<q', t0)
    conn.sendall(msg)

def send_stop(conn: socket.socket):
    msg = struct.pack('<IB', 1, 0x15)        # length=1, CMD_STOP
    conn.sendall(msg)
```

**Runtime handshake order:** After TCP connect, the controller sends CONFIGURE (assigns `device_id`, `strip_length`, `frame_port`) before any LOAD. Until CONFIGURE succeeds, LOAD is rejected with ACK status 2. The CONFIGURE → LOAD → START sequence is the standard startup path.

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

Instead of each ESP running an NTP client, the controller performs a lightweight sync exchange over the device's discovery UDP socket. Sync probes use UDP (not the TCP command channel) because RTT measurement requires minimal, predictable latency — TCP's head-of-line blocking and Nagle's algorithm would add jitter that corrupt offset calculations. The computed offset (`SYNC_RESULT`) is delivered over the TCP command connection since it's a one-shot value, not latency-sensitive.

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
- **boot_token:** uint32_t per-boot token — ensures offsets from a pre-reboot timer are not reused.

Packet format (all fields little-endian):

```
SYNC_REQ:    [type: u8 = 0x01] [seq: u16] [boot_token: u32] [t1: i64]                      -> 15 bytes (UDP)
SYNC_RESP:   [type: u8 = 0x02] [seq: u16] [boot_token: u32] [t1: i64] [t2: i64] [t3: i64]  -> 31 bytes (UDP)
SYNC_RESULT: [type: u8 = 0x03] [seq: u16] [boot_token: u32] [offset: i64]                   -> 15 bytes (TCP, length-prefixed)
```

#### ESP implementation (minimal, non-blocking)

The sync probe exchange (SYNC_REQ/SYNC_RESP) runs on UDP for latency accuracy. The computed result (SYNC_RESULT) arrives over TCP with the other commands.

```cpp
int64_t _sync_offset = 0;    // set by controller via SYNC_RESULT (TCP)
uint32_t _boot_token = 0;    // regenerated on each boot
uint16_t _last_sync_seq = 0; // last applied SYNC_RESULT seq

// Called from poll_udp_sync() — UDP path on the discovery socket
void handle_sync_req(const uint8_t* pkt, const struct sockaddr_in& sender) {
    int64_t t2 = esp_timer_get_time();

    uint8_t resp[31];
    resp[0] = 0x02;  // SYNC_RESP
    memcpy(resp + 1, pkt + 1, 2);   // echo seq
    memcpy(resp + 3, &_boot_token, 4);
    memcpy(resp + 7, pkt + 7, 8);   // echo t1
    memcpy(resp + 15, &t2, 8);
    int64_t t3 = esp_timer_get_time();
    memcpy(resp + 23, &t3, 8);
    sendto(discovery_udp_fd, resp, 31, 0, (struct sockaddr*)&sender, sizeof(sender));
}

// Called from poll_tcp_commands() — TCP path
void handle_sync_result(const uint8_t* payload, uint32_t len) {
    if (len < 14) return;

    uint16_t seq;
    uint32_t boot_token;
    memcpy(&seq, payload, 2);
    memcpy(&boot_token, payload + 2, 4);

    // Drop stale: wrong boot epoch or old/reordered sequence
    if (boot_token != _boot_token || seq < _last_sync_seq)
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

**Startup / reconnect calibration:**

1. Send 8 SYNC_REQ rounds at 1-second intervals.
2. For each accepted round, compute RTT and offset.
3. Build a per-round candidate from the lowest-RTT replies.
4. Feed candidates into a sliding median window.
5. When enough window data exists, compute a smoothed offset and decide whether to send SYNC_RESULT.

**Steady-state (during and between playback):**

Periodic probes at an adaptive interval:

| Condition | Probe interval |
|-----------|---------------|
| Startup calibration | 1s (8 rounds) |
| Healthy synced | 15s |
| Settling / stale recovery | 5s |
| Post-correction confirm probes | 250ms (2 probes) |

At ~10 ESP devices, even 10s intervals are negligible network load (~31 bytes per probe).

#### Controller filter model

```python
class DeviceSync:
    WINDOW_SIZE = 5

    def __init__(self):
        self.window = []            # sliding median window of round candidates
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

        # 7. Apply and schedule fast confirm probes
        self.applied_offset = smoothed
        send_sync_result(device, smoothed)
        self.prev_delta_sign = 0

    def is_stale(self, max_age_s=60):
        """True if no successful sync in max_age_s — trigger full re-sync."""
        ...
```

Key properties of the implemented filter:
- **Low-RTT selection** removes WiFi retransmit noise.
- **Median window** (N=5) makes a single outlier unable to move the output.
- **Sustained-move guard** requires two consecutive deltas in the same direction before applying — prevents toggling from a jitter spike that happens to survive the median.
- **Staleness timeout** triggers a full re-sync burst if samples are consistently bad.

#### Correction behavior during playback

When the ESP receives a `SYNC_RESULT` (over TCP) with an updated offset:

- **ESP clock is early** (offset correction makes `t_rel` smaller -> animation was ahead): next `tick_once()` produces a smaller `t_rel` than expected. The engine effectively stalls for one frame (renders the same visual position twice). Invisible at 20ms frame intervals.
- **ESP clock is late** (offset correction makes `t_rel` larger -> animation was behind): next `tick_once()` produces a larger `t_rel` jump. The engine's cursor naturally skips past finished events — this is a single `tick()` call, no replay needed.

Both directions are handled gracefully by the existing engine design. Corrections filtered through the median window + sustained-move guard are small (a few ms), so the frame-to-frame timing perturbation is imperceptible.

One important implementation detail: receiving sync does **not** re-anchor an already-playing local-fallback session mid-flight. Sync affects the next timing anchor (`START`, `RESUME`, or playing `JUMP`). Until then, an unsynced session keeps using its existing local anchor.

### Simulator clock

ESPSimulated uses `steady_clock` (monotonic) for its tick loop. This is immune to wall-clock jumps from NTP adjustments on the host machine.

For local-only use (no real ESPs): `sync_offset = 0`. The controller and simulator share the same clock domain, so no sync exchange is needed.

For mixed real+simulated setups: the controller treats the simulator as host-locked and reports `0.0 ms` offset in the UI. Real ESPs go through the full sync protocol.

### Seek and virtual time

Two seek mechanisms exist with different tradeoffs:

**CMD_JUMP (all devices):** Resets the engine and starts ticking from a reset-safe time. O(1) — no replay. Valid only within compiler-identified safe intervals where no event carries prior state. See `controller.md`, "Reset-safe jump points".

**CMD_DEBUG_SEEK (simulator only):** Resets the engine and replays frame-by-frame from t=0 to the target. Correct at any arbitrary time, but cost is proportional to the target time. Used for precise scrubbing during development.

### Device outbound UDP frame format

```
[device_id: u16] [gen: u16] [frame_index: u32] [t_rel: f32] [rgb: bytes...]
```

`device_id` is assigned by CONFIGURE and echoed on every outbound UDP packet. CMD_LOAD still carries it redundantly.

### Frame destination

The device sends UDP frames to the controller's IP on a fixed well-known port (`frame_port` from static config, e.g. 9100). The device learns the controller's IP from `getpeername()` on the TCP connection — no additional configuration or handshake needed.

### Controller UDP frame reception

The controller binds a single UDP socket on `frame_port` and receives frames from all devices. Frames are demuxed by the `device_id` field in the packet header, not by source address. This supports multiple simulator instances on the same host (same source IP, different `device_id` values).
