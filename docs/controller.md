# Controller

> **Status: Partially implemented.** The Python controller service, Unix-socket control API, discovery receiver, simulator transport, controller-owned program library, and artifact cache are implemented. `elemctl web` connects as an observer relay and also provides HTTP APIs for layout editing/persistence and device add/edit/remove. Web-side playback/program-control parity and hardware parity are still future work.

## Base-station config

A static config file on the base station is the single source of truth for the physical setup. It defines:

- controller-level runtime settings (`frame_port`, optional `discovery_port`, optional UI/runtime paths)
- the inventory of known devices
- the mapping from logical `strip_id` values used by the DSL to one or more concrete devices

When the server is running, it owns this config as live state. TUI mutations such as `/newdevice` and `/rmdevice` are sent to the server, which validates the change, atomically rewrites the config file, and updates runtime state immediately.

`devices` may be empty. An empty install is valid for config management, discovery status, and program-library operations. Playback loads still require at least one configured device.

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
| `strip_id` | Controller, compiler | Compile | Logical strip name — matches DSL `strip("main_left", ...)`; mirrored devices may share it |
| `length` | Controller, compiler | Compile | Validated against DSL-declared strip length at compile time |

The compile-time fields are `strip_id` and `length`. Runtime fields control discovery, transport, and device identity.

### Duplicate `strip_id` groups

`device_uid` remains globally unique. `strip_id` does not.

Multiple devices may share the same `strip_id` when they are mirrored counterparts of the same logical strip, for example:

- `sim-144` with `strip_id="main"`
- `esp-144` with `strip_id="main"`

All devices sharing a `strip_id` must also share the same configured `length`. This is required so the controller can treat them as one logical strip topology for compilation and cache keys.

### Relationship to the DSL

The DSL declares strip names and lengths: `strip("main_left", length=150)`. Under controller-backed compile paths, it may also omit the length entirely: `strip("main_left")`. The config provides the logical strip length in that case.

The controller validates that a program strip does not exceed the configured logical strip length. Shorter program strips are allowed — the program can intentionally target only the first `N` pixels of a longer configured strip.

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

The compiler exposes manifest-producing APIs directly. The controller does not need to wrap blob dicts into its own structure:

```python
# compile_manifest() / build_manifest() return a CompiledManifest:
@dataclass
class CompiledManifest:
    duration: float                             # seconds
    strips: list[CompiledStripArtifact]         # ordered: name, length, blob
    safe_intervals: list[tuple[float, float]]   # global reset-safe intervals

# build() / compile_program() still return dict[str, bytes] for backwards compat
```

The blob format and decoder are unchanged. The manifest is controller-level metadata — devices never see it. They receive bare blobs via LOAD as before.

### Compilation and caching

Raw `load` recompiles from source on every request. `load_program` resolves a controller-owned library entry and uses an in-memory artifact cache keyed by source hash and logical strip topology.

The topology fingerprint is based on logical `strip_id -> length` groups, not on individual physical devices. Adding a mirrored counterpart with the same `strip_id` and `length` does not invalidate artifact-cache hits.

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

**Gen filtering applies only to RGB frames** forwarded to the client. The controller does not currently ingest device telemetry over the network — end-of-program detection and paused-position tracking are controller-local (derived from `current_t_rel()` on device objects). Device-side `send_telemetry()` is a no-op for `ESPSimulated`; telemetry ingestion may be added when real ESP32 hardware is integrated.

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
```

**Frame assembly policy: complete-only, no deadline sweep.** The controller emits a program frame only when all strips for a given `frame_index` are present. Incomplete buckets are retained until they complete or are cleared by session transitions (`load`, `seek`, `stop`, loop restart). There is no deadline-based stale-bucket sweep — this keeps the implementation simple and avoids timing dependencies.

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
    """Seek always snaps to safe intervals and uses jump()."""
    target = self.snap_to_safe_time(requested_t)
    if target is None:
        return  # no safe interval available
    # Compute shared t0 so all devices and audio are synchronized
    t0 = self.mono_now() - int(target * 1e6)
    self.gen += 1
    for strip in self.active_strips:
        strip.device.jump(t0, target, self.gen)
    # self.audio.seek_and_start_at(target, t0)  # planned

    self.epoch += 1
    self.notify_client(epoch=self.epoch, t_rel=target)

def handle_debug_seek(self, requested_t: float):
    """Separate command — simulator-only arbitrary seek via replay."""
    if not self.supports_debug_seek():
        return  # all active session devices must be simulators
    for strip in self.active_strips:
        strip.device.debug_seek(requested_t)
    self.epoch += 1
    self.notify_client(epoch=self.epoch, t_rel=requested_t)

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

2. **Owns the program library** — scans `controller.animations_dir`, accepts `publish_program` updates, extracts `BEAT` / `DURATION` metadata from known `.py` programs, and exposes the catalog to clients via snapshots and `programs_updated`.

3. **Compiles programs** — either receives raw DSL source (`load`) or resolves a known library entry (`load_program`), compiles it using the Python compiler, validates strip lengths against logical config topology, and produces a manifest with per-strip blobs, duration, and global safe intervals. Library-backed loads use an in-memory artifact cache keyed by source hash and logical strip topology.

4. **Discovers known devices** — listens for UDP HELLO packets, matches them by `device_uid`, caches live `(host, tcp_port)` addresses, and updates known devices in place.

5. **Maintains TCP device connections** — reconnects disconnected devices, sends `CMD_CONFIGURE` after connect, and preserves existing device objects across config changes when possible.

6. **Routes blobs and playback commands** — maps manifest strips by `strip_id` to selected devices, fans one logical strip blob out to mirrored physical targets when needed, sends LOAD/START/JUMP/PAUSE/RESUME/STOP over TCP, and waits for ACK where required.

7. **Manages sessions** — assigns `session_id` on each load, tracks `epoch` (incremented on seek/jump/restart), and exposes current playback state to clients.

8. **Assembles program frames** — receives per-strip RGB frames from simulators and real ESP devices, filters by `gen` (drops stale), groups by `frame_index`, and emits complete multi-strip program frames over UDS.

9. **Owns config mutations** — handles `add_device` / `edit_device` / `remove_device` requests from the TUI, validates them transactionally, atomically rewrites the config file, incrementally reconciles device objects, and rebuilds the controller while idle.

10. **Exposes a control/event API over UDS** — a client (currently the TUI) connects to a Unix Domain Socket to send commands and receive replies, events, snapshots, and program frames. See "Controller ↔ Client protocol" below.

11. **Owns device clock sync** — probes ESP devices over UDP, filters samples, sends `SYNC_RESULT` over TCP, and exposes sync state/offset in device status and snapshots.

### Compilation flow

```python
# Controller resolves a known program id from its library
def load_program(self, program_id: str, targets: list[str] | None = None):
    source, beat, duration = self.program_library.lookup(program_id)

    # 1. Compile (or cache-hit) to a manifest: ordered per-strip blobs + metadata
    manifest = self.compile_or_get_cached(source, beat=beat, duration=duration)

    # 2. Resolve the active physical targets for this load
    #    - if targets is omitted, select all devices whose strip_id is used
    #    - if targets is provided, only that subset participates
    target_groups = self.resolve_targets_by_strip_id(manifest, targets)

    # 3. Validate every strip group in the manifest against selected inventory
    for strip_artifact in manifest.strips:
        self.validate_target_group(strip_artifact, target_groups)

    # 4. Build a controller session and route blobs to the mapped devices
    self.session_id += 1
    self.epoch = 0
    self.gen += 1
    for strip_artifact, group in zip(manifest.strips, target_groups):
        for device in group:
            self.send_load(device.conn, device.device_id, self.gen, strip_artifact.blob)

    # 5. Publish session_start and wait for later play/pause/seek commands
    self.publish_session_start(manifest, target_groups)
```

---

---

## Controller ↔ Client protocol

The controller exposes a **Unix Domain Socket (UDS)** protocol to local clients. The supported topology is **multiple writers plus any number of observers**:

- **Writer** — command-capable client (the TUI, or any client that sends `hello` with `role: "writer"`)
- **Observer** — read-only clients that receive snapshots, events, and frames (for example `elemctl web`)

Multiple writers are allowed. Every writer `hello` triggers `probe_all()` (attempts to connect/reconnect all known devices). All clients share the same ordered event/frame stream from the controller. Replies are point-to-point to the requesting writer connection only.

### Why writers + observers

- **Multiple writers coexist** — the server no longer rejects a second writer.
- **Observers are cheap.** A browser viewer can subscribe without having to reimplement the TUI.
- **Reconnect stays simple.** Each client reconnects independently and gets a fresh snapshot.
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

**Bootstrap handshake:**

Every client must send `hello` as its first command:

```json
{"id": 0, "cmd": "hello", "role": "writer", "protocol_version": 2}
{"id": 0, "cmd": "hello", "role": "observer", "protocol_version": 2}
```

The controller replies with a normal JSON reply. If the role is accepted, it immediately follows with a snapshot. Observer connections that try to send non-`hello` commands receive an error reply.

**Client → Controller (commands):**

Commands carry an `id` (client-assigned, incrementing counter) that the controller echoes in the reply.

```json
{"id": 1, "cmd": "load", "source": "...", "beat": 0.5, "duration": 300.0, "loop": true}
{"id": 2, "cmd": "play"}
{"id": 3, "cmd": "pause"}
{"id": 4, "cmd": "seek", "t_rel": 30.0}
{"id": 5, "cmd": "debug_seek", "t_rel": 7.3}
{"id": 6, "cmd": "stop"}
{"id": 7, "cmd": "status"}
{"id": 8, "cmd": "rescan_programs"}
{"id": 9, "cmd": "load_program", "program_id": "demo_main", "loop": true}
{"id": 10, "cmd": "load_program", "program_id": "demo_main", "targets": ["sim-144", "esp-144"], "loop": true}
{"id": 11, "cmd": "load_scene", "loop": true, "entries": [
  {"program_id": "ambient", "targets": ["sim-1"]},
  {"program_id": "spark", "targets": ["sim-2"]}
]}
{"id": 12, "cmd": "publish_program", "program_id": "demo_main", "source": "...python source..."}
{"id": 13, "cmd": "add_device", "device_type": "sim", "device_uid": "sim-3", "strip_id": "aux", "length": 30}
{"id": 14, "cmd": "edit_device", "target_device_uid": "sim-3", "device_uid": "sim-3", "strip_id": "aux", "length": 60}
{"id": 15, "cmd": "remove_device", "device_uid": "sim-3"}
```

`play` means both fresh start and resume — the controller decides which device command to send based on current state (CMD_START from LOADED/ENDED, CMD_RESUME from PAUSED). The client does not need to distinguish between them.

`status` and the connect-time snapshot are the read path for the current program catalog. `rescan_programs` refreshes the controller-owned library from disk and broadcasts the new catalog to all clients. `publish_program` stores or replaces one known program in that library and broadcasts the updated catalog. Neither command hot-swaps the currently loaded session; new source only takes effect on the next `load_program`.

`edit_device` only allows changing `device_uid`, `strip_id`, and `length`. `device_type` is immutable; changing a device from `sim` to `esp32` is treated as remove + add, not edit.

`load` and `load_program` require at least one configured device. With an empty inventory they fail cleanly with `no configured devices`.

`load_program` accepts an optional `targets` list of `device_uid` values.

- If `targets` is omitted, the controller selects all configured devices whose `strip_id` appears in the compiled program.
- If `targets` is provided, only that subset participates in the session.
- Every program strip must be covered by at least one selected device with matching `strip_id`.
- Selected devices whose `strip_id` is unused by the program are rejected.
- Longer selected devices are allowed; the logical program strip still defines the rendered prefix length and the physical tail stays dark naturally.
- Shorter selected devices are rejected.

Raw `load` remains the legacy unique-topology path. It does not accept `targets`, and it rejects duplicate-`strip_id` topologies with `duplicate strip_id topology requires targeted load support`.

`load_scene` is submitted from the TUI via `/scene FILE`, where `FILE` is a JSON file on disk. The TUI reads the file, wraps its contents with `"cmd": "load_scene"`, and forwards the resulting command to the controller. The scene file contains `entries` and an optional `loop` flag — it does not contain a `cmd` field itself.

**Controller → Client (replies):**

Replies are **controller-complete** — the reply is sent after the controller has finished all work for the command (compilation, device ACKs, state updates). The client does not need to track intermediate states or correlate follow-up events.

```json
{"type": "reply", "id": 1, "ok": true, "result": {"session_id": 42}}
{"type": "reply", "id": 4, "ok": true, "result": {}}
{"type": "reply", "id": 6, "ok": true, "result": {"event": "snapshot", "...": "..."}}
{"type": "reply", "id": 7, "ok": true, "result": {"programs": [{"program_id": "demo_main", "beat": 0.5, "duration": 64.0, "error": null, "strips": ["main"]}]}}
{"type": "reply", "id": 8, "ok": true, "result": {"session_id": 42}}
{"type": "reply", "id": 10, "ok": true, "result": {"session_id": 43}}
{"type": "reply", "id": 11, "ok": true, "result": {"program": {"program_id": "demo_main", "beat": 0.5, "duration": 64.0, "error": null, "strips": ["main"]}}}
{"type": "reply", "id": 1, "ok": false, "error": "device sim-1 rejected blob"}
```

**Controller → Client (events):**

Asynchronous state changes and broadcasts — not tied to a specific command.

```json
{"type": "event", "event": "session_start", "session_id": 42,
 "epoch": 0,
 "duration": 612.0, "safe_intervals": [[0.0, 0.0], [12.4, 13.0], ...],
 "strips": [
   {"name": "main_left", "length": 150,
    "targets": [{"device_id": 1, "device_uid": "sim-left", "device_type": "sim", "length": 150}]},
   {"name": "main_right", "length": 150,
    "targets": [{"device_id": 2, "device_uid": "sim-right", "device_type": "sim", "length": 150}]}
 ]}
{"type": "event", "event": "state", "state": "loaded", "epoch": 0, "session_id": 42}
{"type": "event", "event": "state", "state": "playing", "epoch": 1, "session_id": 42}
{"type": "event", "event": "loop", "epoch": 3, "session_id": 42}
{"type": "event", "event": "device_status",
 "device_id": 1, "device_uid": "sim-1", "strip": "main_left", "length": 150,
 "connected": true, "last_seen": 1711123456.789}
{"type": "event", "event": "device_status",
 "device_id": 1, "device_uid": "sim-1", "strip": "main_left", "length": 150,
 "connected": false, "last_seen": 1711123456.789}
{"type": "event", "event": "programs_updated",
 "programs": [{"program_id": "demo_main", "beat": 0.5, "duration": 64.0, "error": null, "strips": ["main"]}]}
{"type": "event", "event": "error", "message": "load failed: device sim-1 rejected blob"}
```

Note: `session_start` is emitted with `epoch: 0`. A `state` event with `state: "loaded"` is also queued immediately after load. The epoch increments on play, seek, jump, and loop.

- **`session_start`** — new session loaded, includes all metadata for seek bar and frame slicing
- **`state`** — playback state change (playing, paused, ended), includes current epoch
- **`loop`** — program looped back to t=0, includes new epoch
- **`device_status`** — device lifecycle change (connected/disconnected). State-oriented — UI updates indicators
- **`programs_updated`** — program library changed by `rescan_programs` or `publish_program`. Carries the full current catalog so writer and observer clients can refresh without polling
- **`error`** — async failure. Human-oriented — UI shows notification/log. Current implementation emits a single human-readable `message` field.

The `strips` array in `session_start` defines the **canonical logical strip order and lengths** for the session. Program frames pack RGB blobs in this exact order with no per-entry headers — the client uses the strip list to slice the payload. Each strip entry may also include a `targets` array describing the physical devices currently bound to that logical strip.

**Command semantics summary:**

| Command | Controller-complete means | Reply result |
|---------|--------------------------|-------------|
| `load` | Compiled from source, unique-topology legacy load completed, all configured devices ACKed LOAD, session created. Rejected with `no configured devices` when inventory is empty, and with `duplicate strip_id topology requires targeted load support` on mirrored topologies. | `{"session_id": N}` |
| `load_program` | Known program resolved from the controller-owned library, compiled or cache-hit, matching selected devices ACKed LOAD, session created. If `targets` is omitted, all matching devices participate. Rejected with `no configured devices` when inventory is empty. | `{"session_id": N}` |
| `load_scene` | Each entry's `program_id` resolved from the library, compiled or cache-hit independently. One global session created spanning all entries. All entries must have matching durations. Each entry requires explicit non-empty `targets` (device_uids); duplicate targets across entries are rejected. Safe intervals are intersected across all entries (empty intersection is allowed — seek is simply unavailable). | `{"session_id": N}` |
| `publish_program` | Program source validated, then stored under `program_id`, library entry updated, `programs_updated` broadcast queued. Does not load or play the program. Broken source is rejected and not stored. | `{"program": {...}}` |
| `play` | State updated; sends START (from LOADED/ENDED) or RESUME (from PAUSED) to all devices + audio | `{}` |
| `pause` | CMD_PAUSE sent to all devices, audio paused, state updated | `{}` |
| `seek` | Time snapped to safe interval, CMD_JUMP sent to all devices, epoch updated | `{}` |
| `debug_seek` | Simulator-only arbitrary seek via CMD_DEBUG_SEEK. Requires all active session devices to be simulators (`supports_debug_seek()`). Epoch updated. | `{}` |
| `stop` | CMD_STOP sent to all devices, output cleared to black, state updated | `{}` |
| `status` | Snapshot built immediately from current runtime state | snapshot object in `result` |
| `rescan_programs` | Program library rescanned from `animations_dir`, catalog updated, `programs_updated` broadcast queued | `{"programs": [...]}` |
| `add_device` | Candidate config validated, saved atomically, inventory reconciled, controller rebuilt (idle/stopped/ended only) | `{"message": "added device ..."}` |
| `edit_device` | Existing device located by `target_device_uid`, editable fields (`device_uid`, `strip_id`, `length`) validated and saved atomically, inventory reconciled, controller rebuilt (idle/stopped/ended only) | `{"message": "updated device ..."}` |
| `remove_device` | Candidate config validated, saved atomically, inventory reconciled, controller rebuilt (idle/stopped/ended only). Removing the last device is allowed. | `{"message": "removed device ..."}` |

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

After a successful `hello`, the controller immediately sends a **snapshot** — a single JSON message containing everything the client needs to start working. The bootstrap path is: connect → `hello` → reply → snapshot → ready.

The snapshot schema matches `build_snapshot()` in `service.py`:

```json
{
  "type": "event",
  "event": "snapshot",
  "protocol_version": 2,
  "online_count": 2,
  "expected_count": 2,
  "programs": [
    {"program_id": "demo_main", "beat": 0.5, "duration": 64.0, "error": null, "strips": ["main"]}
  ],
  "session": {
    "session_id": 42,
    "epoch": 2,
    "playback_state": "playing",
    "duration": 612.0,
    "current_t_rel": 30.5,
    "safe_intervals": [[0.0, 0.0], [12.4, 13.0], [28.0, 29.5]],
    "strips": [
      {
        "name": "main_left",
        "length": 150,
        "targets": [
          {"device_id": 1, "device_uid": "sim-1", "device_type": "sim", "length": 150}
        ]
      },
      {
        "name": "main_right",
        "length": 150,
        "targets": [
          {"device_id": 2, "device_uid": "sim-2", "device_type": "sim", "length": 150}
        ]
      }
    ]
  },
  "devices": [
    {"device_id": 1, "device_uid": "sim-1", "strip": "main_left", "length": 150, "device_type": "sim", "connected": true, "last_seen": 1711123456.789},
    {"device_id": 2, "device_uid": "sim-2", "strip": "main_right", "length": 150, "device_type": "sim", "connected": true, "last_seen": 1711123456.789}
  ]
}
```

If no active session (controller just started, no program loaded yet):

```json
{
  "type": "event",
  "event": "snapshot",
  "protocol_version": 2,
  "online_count": 0,
  "expected_count": 2,
  "programs": [ ... ],
  "session": null,
  "devices": [ ... ]
}
```

With an empty install, the same snapshot shape is used with `expected_count: 0`, `online_count: 0`, and `devices: []`.

**What the snapshot contains:**

| Field | Purpose |
|-------|---------|
| `protocol_version` | Allows the client to detect incompatible controller versions |
| `online_count` / `expected_count` | Quick device health summary. `expected_count` is currently the configured inventory size. |
| `programs` | Current controller-owned program catalog. Each entry includes `program_id`, extracted `beat`, extracted `duration`, `error` if the file is present but not loadable, and optionally `strips` (list of strip names statically extracted from the DSL source; omitted when extraction is not possible) |
| `session` | Active session if any — includes all metadata needed to render the seek bar and receive frames. `null` if no program is loaded |
| `session.strips` | Canonical logical strip order and lengths — defines how program frame payloads are sliced. Each strip may also include `targets`, the currently bound physical devices. |
| `devices` | Per-device status: `device_id` (numeric), `device_uid` (stable identity), `strip`, `length`, `device_type` (`"sim"` or `"esp32"`), `connected` (boolean) |

After the snapshot, the controller sends incremental events (`state`, `session_start`, etc.) and program frames as they occur. The snapshot is never re-sent mid-connection — it's a connect-time-only message.

### Backpressure

The UDS socket is **non-blocking**. The controller never blocks on frame delivery.

- **Program frames (kind=0x02) are lossy.** If a write returns EAGAIN/EWOULDBLOCK, the frame is dropped silently. The client handles gaps in `frame_index` — it renders whatever arrives next. No backpressure propagates into the device coordination path.
- **JSON messages (kind=0x01) are reliable.** Commands, replies, events, and snapshots are infrequent and small. If a JSON write cannot proceed, the client connection is unhealthy — the controller closes it and waits for reconnect. On reconnect, the client receives a fresh snapshot and resumes.

---

## Web relay

The repo includes `elemctl web`, which has two roles:

**Observer relay** (UDS connection):
- Connects to the controller UDS as an **observer**
- Serves a local HTTP page and WebSocket endpoint
- Relays snapshots, events, and binary program frames to the browser
- Emits `relay_status` events to the browser indicating controller connection state
- Enriches browser snapshots with `relay_version` and layout data

**HTTP APIs** (direct, not through UDS):
- `GET /api/layouts/<device_uid>` — read device layout
- `POST /api/layouts/<device_uid>` — save device layout
- `POST /api/devices` — add device (sends `add_device` command to controller via UDS writer connection)
- `PATCH /api/devices/<uid>` — edit device
- `DELETE /api/devices/<uid>` — remove device

Web-side playback and program-control parity (load, play, seek from the browser) remains future work.

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
   | {"id":2, "cmd":"play"}  |                              |
   |                         |                              |
   |------------------------>|                              |
   |                         |  epoch = 1                   |
   |                         |  TCP: START(t0)              |
   |                         |----------------------------->|
   |                         |                              |  handle_start(t0)
   |                         |                              |  state = PLAYING
```

### 2. Playback (device main loop, autonomous)

```
Device loop iteration (synced session):
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

For an unsynced ESP session, playback uses a local fallback anchor instead of `_sync_offset`. Sync does not re-anchor an already-playing local-fallback session mid-flight; it takes effect on the next `START`, `RESUME`, or playing `JUMP`.

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

## Audio player coordination (planned)

> **Not yet implemented.** The audio player component and its integration with the controller do not exist yet. This section documents the planned contract so that the controller's seek and start flows are specified end-to-end. The LED side (CMD_JUMP, safe intervals, gen filtering) is fully defined; the audio side depends on this interface being implemented.

The audio player would be a separate component on the base station, coordinated by the controller using the same synchronization primitive as device sync: shared absolute `t0` + scheduled start.

### Planned contract

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

Production seek and pause/resume are supported on the LED side — seek is restricted to safe intervals, pause/resume preserves engine state. Audio coordination depends on this contract being implemented.

---

## Device type and debug coordination

Device type is per-device, not a global mode. Each device in the config has a `device_type` field: `"sim"` (ESPSimulated via `network_sim`) or `"esp32"` (real hardware). Debug commands (CMD_DEBUG_SEEK, CMD_DEBUG_STEP) are checked against the **active session devices** via `supports_debug_seek()` — which returns true only when all devices in the current session are `device_type: "sim"`.

- **All-sim topology:** Full debug controls (`debug_seek`/`debug_step`/jump). Supports both arbitrary scrubbing (via CMD_DEBUG_SEEK) and jump-point navigation (via CMD_JUMP).
- **All-esp32 topology:** Controller sends LOAD, START, JUMP, PAUSE, RESUME, STOP, and active sync. No replay-based debug commands. Seek is restricted to safe intervals only.

Both topologies support CMD_JUMP — it works on any device because it only targets times within reset-safe intervals where `reset()` + forward tick is correct.

When the controller sends a debug command (e.g., `debug_seek`), it sends it to all simulators via their TCP connections without waiting for acknowledgment (fire-and-forget). Each simulator independently resets, replays, and sends its RGB frame.

---

## Looping

**Looping is controller-owned.** The device never auto-loops — it transitions to ENDED and goes dark. The controller decides whether and when to restart.

**Mechanism:** `CMD_JUMP(t0, 0.0, new_gen)`. The program is already loaded, t=0 is always safe, and the gen bump filters stale tail frames from the previous iteration. No full LOAD+START cycle needed.

**How it's enabled:** The `load` command accepts a `loop` flag:
```json
{"id": 1, "cmd": "load", "source": "...", "beat": 0.5, "duration": 300.0, "loop": true}
```

**How end-detection works:** The controller does not rely on ENDED telemetry from devices. Instead, it infers end-of-program locally by checking whether every active strip reports `current_t_rel(now) >= duration`. Once all strips have reached the end, the controller issues `jump(now, 0.0, new_gen)` followed by `resume(now)` — because devices are already in the ENDED state and need both a jump (to reset the engine to t=0) and a resume (to begin advancing again from the new time origin).

**Loop event:** The controller emits a loop event to the client so it can reset its playback position:
```json
{"type": "event", "event": "loop", "epoch": 3}
```

**What this means for the device:** Nothing changes. The device doesn't know about looping. It receives JUMP like any other seek, resets its engine, and continues. The looping policy lives entirely in the controller.
