# Controller & Web App

> **Status: Design document.** Not yet implemented. Describes the controller and web app architecture, protocols, identity model, frame assembly, reset-safe intervals, and end-to-end flows.

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
   |  _frame_index++   // base class increments after output_frame()
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

## Looping

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
