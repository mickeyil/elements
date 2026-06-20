# Protocol

All communication between the controller and devices uses three transport channels. A separate unix socket channel connects the controller to local clients (the web app).

```
                        Controller
                       ┌──────────┐
          TCP (cmds)   │          │ unix socket
  Device ◄────────────►│          │◄──────► Web app
          UDP (frames) │          │
  Device ──────────────►          │
          UDP (hello   │          │
           + sync)     │          │
  Device ◄────────────►│          │
                       └──────────┘
```

## TCP Command Protocol (Controller ↔ Device)

Reliable channel for commands and ACKs. Controller opens a persistent connection to each device.

### Wire Format

```
┌──────────────┬──────┬─────────────┐
│ length: u32  │ type │  payload    │
│ (LE)         │ u8   │  (varies)   │
└──────────────┴──────┴─────────────┘
```

`length` includes the type byte but not itself.

### Commands

| Code | Name | Payload | Notes |
|------|------|---------|-------|
| `0x05` | SET_PROFILE | `strip_length: u16` | Configure strip. Must succeed before LOAD. |
| `0x06` | ATTACH | `device_id: u16, frame_port: u16` | Assign wire identity and frame destination. |
| `0x10` | LOAD | `gen: u16, blob: bytes` | Upload blob. Device decodes and enters LOADED. |
| `0x11` | START | `t0: i64` | Begin playback at absolute time t0 (microseconds). |
| `0x12` | JUMP | `t0: i64, t_rel: f32, gen: u16` | Seek to reset-safe time. Resets engine. |
| `0x13` | PAUSE | _(none)_ | Freeze playback. Keep last frame displayed. |
| `0x14` | RESUME | `t0: i64` | Resume from pause with new time origin. No engine reset. |
| `0x15` | STOP | _(none)_ | Clear to black, reset to t=0, stay LOADED. |
| `0x16` | STORE_BACKGROUND | `strip_length: u16, crc32: u32, blob: bytes` | Persist a background blob to device flash (LittleFS). |
| `0x17` | CLEAR_BACKGROUND | _(none)_ | Remove the stored background blob and metadata. |
| `0x18` | QUERY_DEVICE_STATUS | _(none)_ | Return device-reported mode/profile/background inventory in the ACK payload. |
| `0x03` | SYNC_RESULT | `seq: u16, boot_token: u32, offset: i64` | Apply clock correction. |
| `0x22` | DEBUG_SEEK | `t_rel: f32` | Simulator-only: replay from t=0 to target. |
| `0x30` | REBOOT | _(none)_ | Graceful device reboot. |

### ACK Response

```
┌──────────────┬──────────┬──────────┬─────────────────────┐
│ length: u32  │ 0x80     │ status   │ optional payload    │
│ = 2 + N      │ CMD_ACK  │ u8       │ N bytes             │
└──────────────┴──────────┴──────────┴─────────────────────┘
```

Status: `0` = OK, `1` = error, `2` = wrong state (e.g. LOAD before SET_PROFILE).

Devices ACK after SET_PROFILE, ATTACH, LOAD, STORE_BACKGROUND, CLEAR_BACKGROUND, QUERY_DEVICE_STATUS, and REBOOT. Other commands are fire-and-forget (TCP guarantees delivery).

`QUERY_DEVICE_STATUS` returns a 14-byte ACK payload:

```
mode: u8
flags: u8  (bit0 = profile_present, bit1 = background_present)
profile_strip_length: u16
background_strip_length: u16
background_blob_len: u32
background_crc32: u32
```

### Handshake Sequence

```
Controller                          Device
    │                                  │
    │─── SET_PROFILE(strip_length) ──►│
    │◄── ACK(OK) ─────────────────────│
    │                                  │
    │─── ATTACH(device_id, port) ────►│
    │◄── ACK(OK) ─────────────────────│
    │                                  │
    │─── LOAD(gen=1, blob) ──────────►│
    │◄── ACK(OK) ─────────────────────│
    │                                  │
    │─── START(t0) ──────────────────►│
    │                                  │  ← device starts ticking
```

---

## UDP Frame Protocol (Device → Controller)

Fire-and-forget RGB frame stream. Devices send to the controller's `frame_port`. One shared UDP socket on the controller, demuxed by `device_id`.

### Wire Format

```
┌─────────────┬──────────┬──────────────┬───────────┬────────────┐
│ device_id   │ gen      │ frame_index  │ t_rel     │ rgb data   │
│ u16 LE      │ u16 LE   │ u32 LE       │ f32 LE    │ bytes      │
└─────────────┴──────────┴──────────────┴───────────┴────────────┘
```

- `gen` — echoed from last LOAD/JUMP; controller drops frames with stale gen
- `frame_index` — monotonic counter, reset on LOAD/JUMP; used to group multi-strip frames
- `rgb` — 3 bytes per pixel (R, G, B), length = strip_length * 3

The device learns the controller's IP from `getpeername()` on the TCP connection.

---

## UDP Discovery & Sync (Bidirectional, port 6040)

Shares one UDP port for both device discovery and clock synchronization.

### HELLO (Device → Controller)

Sent every 500ms, even after TCP connect (serves as heartbeat).

```
┌────────────┬───────────┬──────────┬──────────────┐
│ magic      │ tcp_port  │ uid_len  │ uid (UTF-8)  │
│ u16 = 0x454C│ u16 LE   │ u8       │ variable     │
└────────────┴───────────┴──────────┴──────────────┘
```

Controller matches `uid` against config. Unknown UIDs are ignored. Duplicate UIDs from different addresses trigger a rejection:

```
┌────────────┬──────────┬──────────┐
│ magic      │ type     │ reason   │
│ u16 = 0x454C│ u8 = 0x01│ u8 = 0x01│  (duplicate UID)
└────────────┴──────────┴──────────┘
```

Device backs off for 5 seconds on rejection.

### Clock Sync

The controller probes devices to measure clock offset. In v3, sims use
the same sync path as firmware; on localhost the measured offset should
be near zero because both endpoints share the host clock domain.

```
Controller                           Device
    │                                  │
    │  SYNC_REQ(seq, boot_token, t1)  │
    │─────────────────────────────────►│
    │                           t2 = now()
    │  SYNC_RESP(seq, token, t1,t2,t3)│
    │◄─────────────────────────────────│  t3 = now()
    │  t4 = now()                      │
    │                                  │
    │  rtt = (t4-t1) - (t3-t2)        │
    │  offset = ((t2-t1) + (t3-t4))/2 │
    │                                  │
    │  SYNC_RESULT(seq, token, offset) │  ← sent over TCP
    │─────────────────────────────────►│
```

Sync packets (all little-endian):

| Packet | Format | Transport |
|--------|--------|-----------|
| SYNC_REQ | `type:u8=0x01, seq:u16, boot_token:u32, t1:i64` (15 bytes) | UDP |
| SYNC_RESP | `type:u8=0x02, seq:u16, boot_token:u32, t1:i64, t2:i64, t3:i64` (31 bytes) | UDP |
| SYNC_RESULT | `type:u8=0x03, seq:u16, boot_token:u32, offset:i64` (15 bytes) | TCP |

The controller applies a sliding median filter (window=5) with a sustained-move guard (two consecutive deltas in the same direction) before sending a correction. Noise floor < 2ms is ignored.

`boot_token` prevents applying offsets from a pre-reboot timer. `seq` prevents applying stale/reordered results.

Sync affects the next START/RESUME/JUMP — it does not re-anchor an already-playing session.

---

## Controller API (over unix socket)

Local Unix Domain Socket (`/tmp/elemctl.sock`). Clients connect as writer (can send commands) or observer (snapshots + events only).

### Wire Format

```
┌──────────────┬──────┬─────────────┐
│ length: u32  │ kind │  payload    │
│ (LE)         │ u8   │  (varies)   │
└──────────────┴──────┴─────────────┘
```

Two kinds:

| Kind | Value | Payload |
|------|-------|---------|
| JSON | `0x01` | UTF-8 JSON (commands, replies, events) |
| Frame | `0x02` | Binary program frame |

### Handshake

Client sends `hello` as first command:

```json
{"id": 0, "cmd": "hello", "role": "writer", "protocol_version": 2}
```

Server replies, then sends a snapshot with full current state (devices, programs, session).

### Commands (Writer → Controller)

```
load, load_program, load_scene, play, pause, stop, seek, debug_seek,
status, rescan_programs, publish_program, provision_background,
clear_background, query_device_status, add_device, edit_device, remove_device,
reboot_device, shutdown
```

Each command carries an `id` that the controller echoes in the reply. Replies are sent after the controller has finished all work for the command.

### Events (Controller → All Clients)

```json
{"type": "event", "event": "session_start", "session_id": 42, "duration": 300.0, ...}
{"type": "event", "event": "state", "state": "playing", "epoch": 1}
{"type": "event", "event": "device_status", "source": "connectivity", "device_uid": "sim-1", "connected": true}
{"type": "event", "event": "device_status", "source": "clock", "device_uid": "esp-246f28b5f190", "clock_state": "synced", ...}
{"type": "event", "event": "device_status", "source": "reported", "device_uid": "esp-246f28b5f190", "reported": {"mode": "detached_background", ...}, ...}
{"type": "event", "event": "device_detached", "device_id": 1, "device_uid": "sim-1", "strip": "main", ...}
{"type": "event", "event": "device_rejoined", "device_id": 1, "device_uid": "sim-1", "strip": "main", ...}
```

`device_status.source` distinguishes connectivity transitions, clock-sync status updates, and device-reported inventory refreshes. Snapshots include `reported` and `reported_at` under each device when the controller has cached a recent device-reported status. `device_detached` fires when a session participant disconnects without aborting the session. `device_rejoined` fires when a detached participant completes live resume and is actively serving again.

### Program Frames (Binary, kind=0x02)

```
┌──────────────┬───────────┬───────────────────────┐
│ frame_index  │ t_rel     │ rgb_strip_0 + strip_1 │
│ u32 LE       │ f32 LE    │ + ... (concatenated)  │
└──────────────┴───────────┴───────────────────────┘
```

Strip order and lengths defined in the `session_start` event. Controller emits a frame only when all strips for a given frame_index are present.

---

## Time Model

Two representations:

- **Absolute** (i64, microseconds) — used in protocol between controller and devices. START carries `t0`. Devices compute `t_rel = (now_mono + sync_offset - t0) / 1e6`.
- **Relative** (f32, seconds) — used inside the engine. `engine.tick(t_rel)`. Precision is ~40µs at 600 seconds, far below the 20ms frame interval.

All protocol timestamps are int64 microseconds, little-endian.
