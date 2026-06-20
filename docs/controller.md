# Controller

The controller is a long-running Python service on the base station. It compiles DSL programs into binary blobs, routes them to devices, orchestrates playback, and exposes a controller API over a unix socket.

## System Overview

```
┌─────────────────────────────────────────────────┐
│                  Base Station                    │
│                                                  │
│  ┌────────────────┐       ┌──────────────────┐  │
│  │   Controller    │ unix │   Web client     │  │
│  │  (compiles,     │socket│  (clients)       │  │
│  │                 │◄─────►│                  │  │
│  │   routes blobs, │       └──────────────────┘  │
│  │   manages       │                             │
│  │   sessions)     │                             │
│  └───────┬─────────┘                             │
│          │ TCP + UDP                             │
└──────────┼───────────────────────────────────────┘
           │ WiFi (same LAN)
    ┌──────┴──────┐
    │   Devices   │
    │ (ESP32 or   │
    │  Simulator) │
    └─────────────┘
```

## What the Controller Does

1. **Compiles programs** — takes DSL source, runs the Python compiler, produces per-strip blobs + metadata (duration, safe intervals)
2. **Manages devices** — discovers devices via UDP HELLO, maintains TCP connections, sends SET_PROFILE and ATTACH on connect
3. **Routes blobs** — maps compiled strips to devices by `strip_id`, fans out to mirrored devices
4. **Orchestrates playback** — LOAD/START/JUMP/PAUSE/RESUME/STOP across all session devices
5. **Assembles frames** — receives per-device RGB frames over UDP, groups by frame_index, emits complete multi-strip program frames to clients
6. **Manages sessions** — tracks session_id (per load), epoch (per discontinuity), generation counter (for stale frame filtering)
7. **Owns config** — reads/writes the static config file, handles add/edit/remove device mutations
8. **Syncs clocks** — probes ESP32 devices via UDP, filters samples, sends corrections over TCP
9. **Serves controller API** — unix socket with writer (commands) and observer (state/frames) roles, both served to the web app

## Config

Repo-local JSON file at `instance/config.json`, the source of truth for the physical setup. On first run, `elemctl` creates it automatically with default controller ports and an empty `devices` list if it does not exist.

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
      "strip_id": "main",
      "length": 60
    }
  ]
}
```

- `device_uid` — stable identity (MAC-based for ESP32, arbitrary for sims)
- `device_type` — `"sim"` or `"esp32"` (determines debug capabilities)
- `host`/`tcp_port` — static endpoint, or empty to await discovery
- `strip_id` — logical name matching DSL `strip()` calls; multiple devices can share one `strip_id` (mirroring)
- `length` — pixel count (1–250), validated against DSL at compile time
- `discovery_port` — `null` disables discovery; defaults to 6040 if omitted
- simulator layouts are stored separately under `instance/layouts/`

## DSL & Compiler

The user writes animations in a Python DSL (`compiler/elements/dsl.py`):

```python
from elements.dsl import *

s = strip("main", length=60, type="RGB")
all_px = s.pixels("0-59")

w = wave(channel="V", h=220, s=1.0, v=0.0, min_val=0.0, max_val=0.4, period=8)
w.schedule(all_px, at=0, duration=4)

sp = spark(color="white", fade=0.1)
sp.schedule(s.pixels("0,15,30,45"), at=0, duration=sec(0.1))

return build(beat=0.5, duration=4)
```

Compilation pipeline (`compiler/elements/compiler.py`):
1. **Validate** — check pixel bounds, required params, strip names
2. **Resolve times** — beats → seconds, time-based params (period, fade, velocity)
3. **Infer layers** — greedy bin-packing of non-overlapping events (max 32 layers)
4. **Pack buffers** — assign shared buffer slots to stateful animations (shift)
5. **Resolve sources** — validate `source=` dependencies, enforce ordering
6. **Compute safe intervals** — find time ranges where engine reset is valid (accounts for source chains); intersect across strips
7. **Emit blob** — binary serialization per strip

Output: `CompiledManifest` with per-strip blobs, duration, and global safe intervals.

## Program Library

The controller scans an animations directory for `.py` programs, extracts `BEAT`/`DURATION` metadata, and exposes a browsable catalog. `load_program` resolves a library entry and uses an artifact cache keyed by source hash + strip topology. `publish_program` stores/replaces programs in the library.

## Session Model

- **session_id** — new on each LOAD. Distinguishes frames from different programs.
- **epoch** — increments on seek/jump/restart. Clients drop frames from old epochs.
- **gen** — u16 on LOAD/JUMP commands, echoed by devices on UDP frames. Controller filters stale in-flight frames by gen mismatch.

### Controller-Owned Session Timebase

The controller owns the canonical show clock via `_play_t0_ns` — it does not derive time from devices. All playback decisions (current_t_rel, pause position, end-of-program, loop restart) use `_session_t_rel(now)`. Devices follow the controller's timeline; they are not the authority for show progress.

### Retained Sessions and Participant State

A session survives individual device disconnects. Each participant has three states:

- **session member** — part of the retained session (in `_active_strips`)
- **transport attached** — TCP connection is up (SET_PROFILE + ATTACH succeeded)
- **actively serving** — loaded with the session blob and executing playback commands

When a device disconnects, it's marked detached (not attached, not serving) but stays a session member. The session continues on the controller-owned clock. Observer frames suspend when any manifest strip has no actively serving, frame-producing participant.

When a device reconnects, it's marked transport-attached. Live resume loads the retained session blob and issues state-appropriate commands. For ESP32 devices in PLAYING or PAUSED sessions, resume waits until a sync correction has been sent for the current boot token. In LOADED and STOPPED, ESP32 devices rejoin immediately (no sync-sensitive commands).

| Session state | Action |
|---------------|--------|
| PLAYING | LOAD + JUMP to safe point + RESUME |
| PAUSED | LOAD + JUMP to paused position |
| LOADED | LOAD only |
| STOPPED | LOAD + STOP |
| ENDED | No resume (v1 policy) |

### Background Provisioning

The controller can provision a background blob to an ESP32 device via `provision_background`. This stores the currently loaded session's compiled blob as the device's persistent fallback animation. The device attempts to play it at boot (before WiFi/discovery, self-applying the stored strip_length as its hardware profile if none is set) and resumes it after controller detach (grace hold → blank → background). Controller attachment preempts background playback. `clear_background` removes the stored blob.

## Safe Intervals & Seek

The compiler identifies time ranges where no event carries prior state, making engine reset safe. The controller snaps seek requests to the nearest safe interval and uses JUMP (O(1) on all devices). Debug seek (simulator only) replays from t=0 for arbitrary precision. Live resume uses a forward-looking safe-point search (next safe interval at or after current time).

## Key Files

| File | Role |
|------|------|
| `controller/elemctl/service.py` | Core service logic: command handlers, compilation, device lifecycle |
| `controller/elemctl/server.py` | Unix socket server, client management, tick loop |
| `controller/elemctl/controller.py` | Python controller state machine |
| `controller/elemctl/network_device.py` | TCP/UDP device transport (lazy connect, optimistic state) |
| `controller/elemctl/config.py` | Config loading and validation |
| `controller/elemctl/config_edit.py` | Atomic config mutations (add/edit/remove device) |
| `controller/elemctl/discovery.py` | UDP HELLO packet parsing and matching |
| `controller/elemctl/clock_sync.py` | NTP-like sync: probe, filter, correct |
| `controller/elemctl/udp_receiver.py` | Shared UDP socket, per-device-id frame routing |
| `controller/elemctl/device_protocol.py` | Device protocol encode/decode |
| `controller/elemctl/controller_protocol.py` | Controller protocol encode/decode |
| `controller/elemctl/library.py` | Program catalog, metadata extraction, artifact cache |
| `controller/elemctl/run.py` | Standalone playback runner (no persistent service) |
| `controller/elemctl/sim.py` | Legacy launcher for deprecated `network_sim` processes; v3 sim launcher work is pending |
| `compiler/elements/dsl.py` | User-facing DSL functions |
| `compiler/elements/compiler.py` | Full compilation pipeline |
| `compiler/elements/blob.py` | Binary blob serialization/deserialization |
| `compiler/elements/types.py` | Shared types and constants |

## Entry Point

```bash
./elemctl server                        # start controller service using instance/config.json
./elemctl web                           # start web UI server (load + playback control)
./elemctl run program.py                # standalone: compile + play
```

`./elemctl sim` still points at the deprecated `network_sim` path. The
v3 sim launcher is not the active smoke-test path yet.

`elemctl` is a self-bootstrapping script that creates/maintains a managed venv at `local/venv`.
