# Controller

> **Status: Partially implemented.** The Python controller service, Unix-socket control API, discovery receiver, and `network_sim` transport are implemented. The web app described in earlier design drafts is not part of this repo. Artifact caching is not implemented — each `load` recompiles from source.

## Base-station config

A static config file on the base station is the single source of truth for the physical setup. It defines:

- controller-level runtime settings (`frame_port`, optional `discovery_port`, optional UI/runtime paths)
- the inventory of known devices
- the mapping from logical `strip_id` values used by the DSL to concrete devices

When the server is running, it owns this config as live state. TUI mutations such as `/newdevice` and `/rmdevice` are sent to the server, which validates the change, atomically rewrites the config file, and updates runtime state immediately.

### Example config

```json
{
  "controller": {
    "frame_port": 9002,
    "discovery_port": 6040
  },
  "devices": [
    {
      "device_id": 1,
      "device_uid": "sim-1",
      "device_type": "sim",
      "host": "",
      "tcp_port": 0,
      "strip_id": "main_left",
      "length": 150
    },
    {
      "device_id": 2,
      "device_uid": "sim-2",
      "device_type": "sim",
      "host": "",
      "tcp_port": 0,
      "strip_id": "main_right",
      "length": 150
    }
  ]
}
```

### What the config provides

| Field | Used by | Scope | Purpose |
|-------|---------|-------|---------|
| `controller.frame_port` | Controller, devices | Runtime | UDP port where devices send RGB frames |
| `controller.discovery_port` | Controller, devices | Runtime | UDP port for discovery HELLO packets (`6040` by default, `null` disables discovery) |
| `device_id` | Controller, device transport | Runtime | Numeric wire identifier echoed on UDP frames |
| `device_uid` | Controller, discovery | Runtime | Stable device identity (for example a MAC or `sim-1`) |
| `device_type` | Controller | Runtime | `"sim"` or `"esp32"` — determines debug capability and topology rules |
| `host` / `tcp_port` | Controller | Runtime | Static endpoint when discovery is disabled; `host=""` + `tcp_port=0` means "await discovery" |
| `strip_id` | Controller, compiler | Compile | Logical strip name — matches DSL `strip("main_left", ...)` |
| `length` | Controller, compiler | Compile | Validated against DSL-declared strip length at compile time |

The compile-time fields are `strip_id` and `length`. Runtime fields control discovery, transport, and device identity.

### Relationship to the DSL

The DSL declares strip names and lengths: `strip("main_left", length=150)`. The config maps those names to physical devices. The controller validates at compile time that DSL-declared lengths match config-declared lengths. If they disagree, compilation fails with a clear error.

### Relationship to device discovery

The config is the inventory of known devices and strip topology. Discovery fills in live network addresses for those known devices at runtime.

- `device_uid` is the stable hardware identity (for example a MAC suffix on real hardware, or `sim-1` for a simulator)
- `strip_id` and `length` are controller-owned strip configuration
- the controller listens for UDP HELLO packets on `discovery_port`
- when `discovery_port` is omitted from config, it defaults to `6040`
- when `discovery_port` is `null`, discovery is disabled and devices must use static `host` + `tcp_port`

Discovery does not add unknown devices automatically. Unknown `device_uid` values are ignored until they are added to config.

---

## Program manifest and identity

### Compiler return type

The compiler produces a **manifest** — not just blobs, but metadata about the compiled program:

```python
# What compile_program() currently returns:
dict[str, bytes]   # strip_name -> blob

# What the controller wraps it into:
{
    "duration": 612.0,                 # seconds
    "safe_intervals": [(0.0, 0.0), (12.4, 13.0), (28.0, 29.5), (44.5, 46.0), (58.0, 60.0)],  # reset-safe intervals (global)
    "strips": [                              # ordered list — defines canonical strip order
        { "name": "main_left",  "length": 150, "blob": b"..." },
        { "name": "main_right", "length": 150, "blob": b"..." }
    ]
}
```

The blob format and decoder are unchanged. The manifest is controller-level metadata — devices never see it. They receive bare blobs via LOAD as before.

### Compilation (no caching)

Each `load` command recompiles from source. There is no artifact cache — the controller calls the compiler on every load request. Caching compiled manifests is a potential future optimization but is not implemented.

### Identity model

Two levels of identity track what's loaded and what's happening:

| Identity | What it means | When it changes |
|----------|---------------|-----------------|
| **session_id** | Active load — "which live run is this?" | New on each LOAD |
| **epoch** | Continuity marker — "has playback been interrupted?" | Increments on seek, restart, jump |

**Why session_id matters:** When the controller loads a new program, old frames from the previous program may still be in UDP flight. Without session_id, the client can't distinguish stale frames from current ones.

**Why epoch matters:** Within a session, seek/jump creates a discontinuity. Frames from before the seek have the old epoch; frames after have the new epoch. The client drops frames with an epoch lower than the current one.

### Example flow

```
Controller                              Client
    |                                        |
    |  load_program("song_abc")              |
    |  session_id = 42                       |
    |                                        |
    |  { "event": "session_start",           |
    |    "session_id": 42,                   |
    |    "duration": 612.0,                  |
    |    "safe_intervals": [...],             |
    |    "strips": [                         |
    |      {"name":"main_left","length":150},|
    |      {"name":"main_right","length":150}|
    |    ] }                                 |
    |--------------------------------------->|
    |                                        |  client renders seek bar
    |                                        |  strips[] defines order and
    |  play()                                |     with safe interval markers
    |  epoch = 1                             |
    |                                        |
    |  program frame (binary):               |
    |  [frame_index=10] [t_rel=0.34]         |
    |  [rgb_strip_0] [rgb_strip_1]           |
    |--------------------------------------->|  client renders
    |                                        |
    |  ... frames flow ...                   |
    |                                        |
    |  seek(30.0) -> snaps to 28.0           |
    |  epoch = 2                             |
    |                                        |
    |  late frame from epoch 1 arrives       |
    |--------------------------------------->|  client drops (epoch < 2)
    |                                        |
    |  frame from epoch 2 arrives            |
    |--------------------------------------->|  client renders
```

### Generation counter and frame filtering

Devices don't know about session_id or epoch — those are controller-level concepts. But the controller can't simply stamp its current epoch onto incoming UDP frames, because stale frames emitted before a LOAD or JUMP may arrive after the controller has already advanced its epoch. Those stale frames would be incorrectly stamped with the new epoch.

To solve this, LOAD and JUMP commands carry a **generation counter** (`gen`, u16) that the device stores and echoes on every outbound UDP frame.

**When the device starts using the new gen:** synchronously in the command handler, before the next `tick_once()` / `output_frame()` cycle. For `handle_jump()`, this is immediate. For `handle_load()`, `_gen` is set only after successful decode — on decode failure the device clears to black and calls `output_frame()` (so the strip goes dark), then transitions to IDLE. That black frame carries the old gen, not the new one, since `_gen` is only updated on success. The first frame emitted after a successful command carries the new gen; any frames already in the UDP pipeline carry the old gen. This is the invariant that makes controller-side filtering work.

**Telemetry is not gen-filtered.** Gen filtering applies to RGB frames forwarded to the client. Telemetry (health, errors, decode failures) is always accepted by the controller regardless of gen — otherwise the controller would never learn about a failed LOAD.

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
    """Send assembled program frame to client over UDS."""
    # Binary payload: [frame_index:u32] [t_rel:f32] [rgb_0][rgb_1]...[rgb_N-1]
    # Strip order and lengths defined in session snapshot
    payload = struct.pack('<If', frame_index, bucket["t_rel"])
    for i in range(self.strip_count):
        payload += bucket["strips"][i]
    self.send_to_client(kind=0x02, payload=payload)

# Periodic cleanup: drop incomplete frames past their deadline
def sweep_stale_frames(self):
    now = time.monotonic()
    for fid in list(self.pending_frames):
        if self.pending_frames[fid]["deadline"] < now:
            del self.pending_frames[fid]  # incomplete — drop entire frame
```

**Frame assembly policy: complete-only.** The controller emits a program frame only when all strips for a given `frame_index` are present. If any strip is missing when the deadline expires, the entire frame is dropped. This keeps semantics clean — the client only sees coherent frames. One slow device causes dropped frames, not stale-filled partial renders.

**Scope:** `gen` filtering applies to LOAD and JUMP only. CMD_DEBUG_SEEK (simulator-only) does not bump `gen`. A stale frame from just before a debug seek could be stamped with the new epoch, but this is at most a single-frame glitch during interactive dev scrubbing — not worth coupling the debug path to the gen protocol.

### What carries session_id and epoch

Session_id and epoch are controller-level metadata, communicated to the client via JSON events — not embedded in binary program frames. The session_start event establishes context; epoch changes are sent as separate events. The client tracks the current session_id and epoch and drops any late-arriving data from a previous epoch.

Program frames (binary, kind=0x02) carry only `frame_index` and `t_rel` — they are implicitly within the current session/epoch because they flow on the same ordered UDS connection as the events.

---

## Reset-safe jump points

### Definition

A time `t` is **reset-safe** if `engine.reset()` followed by `engine.tick(t)` produces correct output without replaying earlier frames. Safe times form **intervals**, not discrete points — a continuous range `[gap_start, gap_end)` where every time is reset-safe. Modeling these as intervals is essential for correct multi-strip intersection.

### Why this matters

The engine's cursor is forward-only. To seek to an arbitrary time, the simulator replays from t=0 — expensive and impractical on real ESP hardware. But if the target time falls within a safe interval, the engine can simply reset and start ticking from there. Cost is O(1) instead of O(target_time).

### What creates history-dependence

**Active events** — the engine creates animation instances on the first tick where an event is active. Jumping into the middle of an active event skips that creation.

**Source dependencies** — a shift with `source=paint` snapshots the source layer's buffer at construction. If the source event (paint) ran earlier and has ended, its output must still be in the layer buffer when the shift activates. Jumping past the source event means the shift snapshots a zeroed buffer.

Simply checking for "no active event" is insufficient. A gap between a source event and its dependent event appears idle but is actually unsafe — the source's buffer state must survive across the gap. The compiler handles this by extending each event's unsafe span backward through its source dependency chain (`required_start_sec`). See `docs/compiler.md` § 8 for the algorithm.

### Compiler analysis

The compiler computes safe intervals as part of the compilation pipeline. Per-strip intervals are computed internally and intersected across all strips. Only the global result is exposed on `CompiledManifest.safe_intervals`.

The controller receives the pre-computed global safe intervals from the manifest — it does not compute them itself.

If the user seeks to 4.7, it falls within `(4.5, 5.0)` — a valid jump target. If they seek to 6.5, the controller snaps to the nearest safe interval.

### Controller seek logic

```python
def handle_seek(self, requested_t: float):
    if self.all_devices_are_sim():
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
    self.notify_client(epoch=self.epoch, t_rel=target)

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

### Client seek bar

The controller sends session metadata (including safe intervals) to the client when a program is loaded. The client can render safe regions on a seek bar:

- **All-sim topology:** allow arbitrary scrubbing, safe intervals shown as visual highlights
- **All-esp32 topology:** restrict seek to safe intervals only

### Edge case: programs with few or no interior safe intervals

A program with a single long event spanning the full duration (e.g., one wave from 0 to 300s) has only `[(0.0, 0.0)]` as a safe interval — the degenerate t=0-only case. This is immediately visible from the metadata — the UI can disable the seek bar or show "no safe jump regions available." In practice, music-synced programs have frequent phrase boundaries with brief blackouts, producing many safe intervals.

---

## Controller responsibilities

The controller is the long-running authority on the base station.

### What the controller does

1. **Loads config and owns it as live state** — reads the base-station config file at startup, keeps the raw config document in memory, and treats it as the authoritative inventory while running.

2. **Compiles programs** — receives DSL source (from a client such as the TUI), compiles it using the Python compiler, validates strip lengths against config, and produces a manifest with per-strip blobs, duration, and global safe intervals. No artifact cache yet — each `load` recompiles from source.

3. **Discovers known devices** — listens for UDP HELLO packets, matches them by `device_uid`, caches live `(host, tcp_port)` addresses, and updates known devices in place.

4. **Maintains TCP device connections** — reconnects disconnected devices, sends `CMD_CONFIGURE` after connect, and preserves existing device objects across config changes when possible.

5. **Routes blobs and playback commands** — maps manifest strips by `strip_id` to devices, sends LOAD/START/JUMP/PAUSE/RESUME/STOP over TCP, and waits for ACK where required.

6. **Manages sessions** — assigns `session_id` on each load, tracks `epoch` (incremented on seek/jump/restart), and exposes current playback state to clients.

7. **Assembles program frames** — receives per-strip RGB frames from simulators, filters by `gen` (drops stale), groups by `frame_index`, and emits complete multi-strip program frames over UDS.

8. **Owns config mutations** — handles `add_device` / `remove_device` requests from the TUI, validates them transactionally, atomically rewrites the config file, incrementally reconciles device objects, and rebuilds the controller while idle.

9. **Exposes a control/event API over UDS** — a client (currently the TUI) connects to a Unix Domain Socket to send commands and receive replies, events, snapshots, and program frames. See "Controller ↔ Client protocol" below.

10. **May sync clocks later** — the custom clock sync protocol (SYNC_REQ/SYNC_RESP/SYNC_RESULT) is documented in `transport.md` but not yet implemented.

### Compilation flow

```python
# Controller receives DSL source from client
def load_program(self, dsl_source: str, beat: float, duration: float):
    # 1. Compile to a manifest: ordered per-strip blobs + metadata
    manifest = compile_manifest_from_dsl(dsl_source, beat=beat, duration=duration)

    # 2. Validate every strip in the manifest against configured inventory
    for strip_artifact in manifest.strips:
        device = self.device_for_strip(strip_artifact.strip_id)  # by strip_id

    # 3. Build a controller session and route blobs to the mapped devices
    self.session_id += 1
    self.epoch = 0
    self.gen += 1
    for strip_artifact in manifest.strips:
        device = self.device_for_strip(strip_artifact.strip_id)
        self.send_load(device.conn, device.device_id, self.gen, strip_artifact.blob)

    # 4. Publish session_start and wait for later play/pause/seek commands
    self.publish_session_start(manifest)
```

---

---

## Controller ↔ Client protocol

A single **Unix Domain Socket (UDS)** connection carries all traffic between the controller and a client (currently the TUI; a future web app would use the same protocol). One connection, one ordering domain, one backpressure story.

**Exactly one client.** The controller accepts one UDS connection at a time. This avoids command arbitration and multi-client state fanout on the controller side.

### Why one connection

- **Ordering is guaranteed.** `session_start` → `epoch_changed` → first program frame always arrive in that order. Two sockets would create cross-stream ordering races.
- **Reconnect is simple.** Reconnect → receive snapshot → resume frame flow. No need to synchronize multiple connections.
- **No external dependencies.** Just a Unix socket with length-prefixed records.

### Wire format

```
[length: u32 LE] [kind: u8] [payload...]
```

`length` includes the kind byte but not itself (same convention as the device TCP protocol).

Two record kinds:

#### kind=0x01 — JSON

`payload` is UTF-8 JSON.

- Commands are plain JSON objects with a `cmd` field.
- Replies and events include a `type` field (`"reply"` or `"event"`).

**Client → Controller (commands):**

Commands carry an `id` (client-assigned, incrementing counter) that the controller echoes in the reply.

```json
{"id": 1, "cmd": "load", "source": "...", "beat": 0.5, "duration": 300.0, "loop": true}
{"id": 2, "cmd": "play"}
{"id": 3, "cmd": "pause"}
{"id": 4, "cmd": "seek", "t_rel": 30.0}
{"id": 5, "cmd": "stop"}
{"id": 6, "cmd": "status"}
{"id": 7, "cmd": "add_device", "device_type": "sim", "device_uid": "sim-3", "strip_id": "aux", "length": 30}
{"id": 8, "cmd": "remove_device", "device_uid": "sim-3"}
```

`play` means both fresh start and resume — the controller decides which device command to send based on current state (CMD_START from LOADED/ENDED, CMD_RESUME from PAUSED). The client does not need to distinguish between them.

**Controller → Client (replies):**

Replies are **controller-complete** — the reply is sent after the controller has finished all work for the command (compilation, device ACKs, state updates). The client does not need to track intermediate states or correlate follow-up events.

```json
{"type": "reply", "id": 1, "ok": true, "result": {"session_id": 42}}
{"type": "reply", "id": 4, "ok": true, "result": {}}
{"type": "reply", "id": 6, "ok": true, "result": {"event": "snapshot", "...": "..."}}
{"type": "reply", "id": 7, "ok": true, "result": {"message": "added device sim-3"}}
{"type": "reply", "id": 1, "ok": false, "error": "device sim-1 rejected blob"}
```

**Controller → Client (events):**

Asynchronous state changes and broadcasts — not tied to a specific command.

```json
{"type": "event", "event": "session_start", "session_id": 42,
 "epoch": 1,
 "duration": 612.0, "safe_intervals": [[0.0, 0.0], [12.4, 13.0], ...],
 "strips": [{"name": "main_left", "length": 150}, {"name": "main_right", "length": 150}]}
{"type": "event", "event": "state", "state": "playing", "epoch": 2, "session_id": 42}
{"type": "event", "event": "loop", "epoch": 3, "session_id": 42}
{"type": "event", "event": "device_status",
 "device_id": 1, "device_uid": "sim-1", "strip": "main_left", "length": 150, "connected": true}
{"type": "event", "event": "device_status",
 "device_id": 1, "device_uid": "sim-1", "strip": "main_left", "length": 150, "connected": false}
{"type": "event", "event": "error", "message": "load failed: device sim-1 rejected blob"}
```

- **`session_start`** — new session loaded, includes all metadata for seek bar and frame slicing
- **`state`** — playback state change (playing, paused, ended), includes current epoch
- **`loop`** — program looped back to t=0, includes new epoch
- **`device_status`** — device lifecycle change (connected/disconnected). State-oriented — UI updates indicators
- **`error`** — async failure. Human-oriented — UI shows notification/log. Current implementation emits a single human-readable `message` field.

The `strips` array in `session_start` defines the **canonical strip order and lengths** for the session. Program frames pack RGB blobs in this exact order with no per-entry headers — the client uses the strip list to slice the payload.

**Command semantics summary:**

| Command | Controller-complete means | Reply result |
|---------|--------------------------|-------------|
| `load` | Compiled from source, all devices ACKed LOAD, session created | `{"session_id": N}` |
| `play` | State updated; sends START (from LOADED/ENDED) or RESUME (from PAUSED) to all devices + audio | `{}` |
| `pause` | CMD_PAUSE sent to all devices, audio paused, state updated | `{}` |
| `seek` | Time resolved/snapped, JUMP or DEBUG_SEEK sent, epoch updated | `{}` |
| `stop` | CMD_STOP sent to all devices, output cleared to black, state updated | `{}` |
| `status` | Snapshot built immediately from current runtime state | snapshot object in `result` |
| `add_device` | Candidate config validated, saved atomically, inventory reconciled, controller rebuilt (idle/stopped/ended only) | `{"message": "added device ..."}` |
| `remove_device` | Candidate config validated, saved atomically, inventory reconciled, controller rebuilt (idle/stopped/ended only) | `{"message": "removed device ..."}` |

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

On every connect (first or reconnect), the controller immediately sends a **snapshot** — a single JSON message containing everything the client needs to start working. No request needed, no handshake. The client has one bootstrap path: connect → receive snapshot → ready.

The snapshot schema matches `build_snapshot()` in `service.py`:

```json
{
  "type": "event",
  "event": "snapshot",
  "protocol_version": 1,
  "online_count": 2,
  "expected_count": 2,
  "session": {
    "session_id": 42,
    "epoch": 2,
    "playback_state": "playing",
    "duration": 612.0,
    "current_t_rel": 30.5,
    "safe_intervals": [[0.0, 0.0], [12.4, 13.0], [28.0, 29.5]],
    "strips": [
      {"name": "main_left", "length": 150},
      {"name": "main_right", "length": 150}
    ]
  },
  "devices": [
    {"device_id": 0, "device_uid": "sim-1", "strip": "main_left", "length": 150, "device_type": "sim", "connected": true},
    {"device_id": 1, "device_uid": "sim-2", "strip": "main_right", "length": 150, "device_type": "sim", "connected": true}
  ]
}
```

If no active session (controller just started, no program loaded yet):

```json
{
  "type": "event",
  "event": "snapshot",
  "protocol_version": 1,
  "online_count": 0,
  "expected_count": 2,
  "session": null,
  "devices": [ ... ]
}
```

**What the snapshot contains:**

| Field | Purpose |
|-------|---------|
| `protocol_version` | Allows the client to detect incompatible controller versions |
| `online_count` / `expected_count` | Quick device health summary. `expected_count` is currently the configured inventory size. |
| `session` | Active session if any — includes all metadata needed to render the seek bar and receive frames. `null` if no program is loaded |
| `session.strips` | Canonical strip order and lengths — defines how program frame payloads are sliced |
| `devices` | Per-device status: `device_id` (numeric), `device_uid` (stable identity), `strip`, `length`, `device_type` (`"sim"` or `"esp32"`), `connected` (boolean) |

After the snapshot, the controller sends incremental events (`state`, `session_start`, etc.) and program frames as they occur. The snapshot is never re-sent mid-connection — it's a connect-time-only message.

### Backpressure

The UDS socket is **non-blocking**. The controller never blocks on frame delivery.

- **Program frames (kind=0x02) are lossy.** If a write returns EAGAIN/EWOULDBLOCK, the frame is dropped silently. The client handles gaps in `frame_index` — it renders whatever arrives next. No backpressure propagates into the device coordination path.
- **JSON messages (kind=0x01) are reliable.** Commands, replies, events, and snapshots are infrequent and small. If a JSON write cannot proceed, the client connection is unhealthy — the controller closes it and waits for reconnect. On reconnect, the client receives a fresh snapshot and resumes.

---

## Future work: Web app and browser protocol

> The web app (a separate process serving browser assets and relaying between the controller and the browser) is not yet implemented. The controller exposes a UDS-based client protocol (see above) that a future web app would connect to.

---

## Data flow: end-to-end example

A wave animation on two strips, controller + two ESPSimulated devices.

### 1. Startup

```
Client                    Controller                     ESPSimulated
   |                         |                              |
   | {"id":1,                |                              |
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
   |  _frame_index++   // base class increments after output_frame()
   |
   |  sleep until next frame (20ms cadence)
```

### 3. Controller assembles program frame and forwards to client

```
ESPSimulated (strip 0)    ESPSimulated (strip 1)    Controller              Client
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
   |                         |                         |     (via UDS)        |
   |                         |                         |                      | render canvas
```

### 4. Jump (client seeks within a safe region)

```
Client                    Controller                Devices (all)
   |                         |                         |
   | {"id":3,                |                         |
   |  "cmd":"seek",          |                         |
   |  "t_rel":2.5}           |                         |
   |------------------------>|                         |
   |                         |  2.5 is within safe      |
   |                         |  interval [2.0, 3.0)    |
   |                         |  epoch = 2              |
   |                         |                         |
   |                         |  gen += 1                |
   |                         |  TCP: CMD_JUMP(t0, 2.5, gen)
   |                         |------------------------>|
   |                         |                         |  handle_jump(t0, 2.5, gen):
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

When all devices are simulators, the controller sends CMD_DEBUG_SEEK for arbitrary positions:

```
Client                    Controller                ESPSimulated
   |                         |                         |
   | {"id":4,                |                         |
   |  "cmd":"debug_seek",    |                         |
   |  "t_rel":7.3}           |                         |
   |------------------------>|                         |
   |                         |  (all-sim topology)     |
   |                         |  epoch = 3              |
   |                         |  TCP: DEBUG_SEEK(7.3)   |
   |                         |------------------------>|
   |                         |                         |  debug_seek(7.3):
   |                         |                         |    engine.reset()
   |                         |                         |    replay 0 -> 7.3
   |                         |                         |    output_frame()
   |                         |                         |    _frame_index++
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

## Device type and debug coordination

Device type is per-device, not a global mode. Each device in the config has a `device_type` field: `"sim"` (ESPSimulated via `network_sim`) or `"esp32"` (real hardware, planned). Debug commands (CMD_DEBUG_SEEK, CMD_DEBUG_STEP) require an all-simulator topology — the controller checks that every device is `device_type: "sim"` before sending debug commands.

- **All-sim topology:** Full debug controls (seek/step/jump). Supports both arbitrary scrubbing (via CMD_DEBUG_SEEK) and jump-point navigation (via CMD_JUMP).
- **All-esp32 topology (planned):** Controller sends LOAD, START, JUMP, PAUSE, RESUME, STOP, and (when implemented) sync. No replay-based debug commands. Seek is restricted to safe intervals only.

Both topologies support CMD_JUMP — it works on any device because it only targets times within reset-safe intervals where `reset()` + forward tick is correct.

When the controller sends a debug command (e.g., seek), it sends it to all simulators via their TCP connections without waiting for acknowledgment (fire-and-forget). Each simulator independently resets, replays, and sends its RGB frame.

---

## Looping

**Looping is controller-owned.** The device never auto-loops — it transitions to ENDED and goes dark. The controller decides whether and when to restart.

**Mechanism:** `CMD_JUMP(t0, 0.0, new_gen)`. The program is already loaded, t=0 is always safe, and the gen bump filters stale tail frames from the previous iteration. No full LOAD+START cycle needed.

**How it's enabled:** The `load` command accepts a `loop` flag:
```json
{"id": 1, "cmd": "load", "source": "...", "beat": 0.5, "duration": 300.0, "loop": true}
```

**How end-detection works:** When a device's program finishes, it transitions to ENDED and sends telemetry. The controller treats this as a program-level signal — it does not react per-device. Once the controller determines the program has ended (first ENDED telemetry, since all devices share the same t0 and duration), it issues one coordinated JUMP to all devices with a shared t0 and new gen.

**Loop event:** The controller emits a loop event to the client so it can reset its playback position:
```json
{"type": "event", "event": "loop", "epoch": 3}
```

**What this means for the device:** Nothing changes. The device doesn't know about looping. It receives JUMP like any other seek, resets its engine, and continues. The looping policy lives entirely in the controller.
