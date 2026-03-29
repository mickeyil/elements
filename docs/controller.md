# Controller

The controller is a long-running Python service on the base station. It compiles DSL programs into binary blobs, routes them to devices, orchestrates playback, and exposes a control API over a Unix Domain Socket.

## System Overview

```
┌─────────────────────────────────────────────────┐
│                  Base Station                    │
│                                                  │
│  ┌────────────────┐       ┌──────────────────┐  │
│  │   Controller    │  UDS  │   TUI / Web      │  │
│  │  (compiles,     │◄─────►│  (clients)       │  │
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
2. **Manages devices** — discovers devices via UDP HELLO, maintains TCP connections, sends CONFIGURE on connect
3. **Routes blobs** — maps compiled strips to devices by `strip_id`, fans out to mirrored devices
4. **Orchestrates playback** — LOAD/START/JUMP/PAUSE/RESUME/STOP across all session devices
5. **Assembles frames** — receives per-device RGB frames over UDP, groups by frame_index, emits complete multi-strip program frames to clients
6. **Manages sessions** — tracks session_id (per load), epoch (per discontinuity), generation counter (for stale frame filtering)
7. **Owns config** — reads/writes the static config file, handles add/edit/remove device mutations
8. **Syncs clocks** — probes ESP32 devices via UDP, filters samples, sends corrections over TCP
9. **Serves control API** — UDS socket with writer (TUI) and observer (web) roles

## Config

Single JSON file, the source of truth for the physical setup:

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

## Safe Intervals & Seek

The compiler identifies time ranges where no event carries prior state, making engine reset safe. The controller snaps seek requests to the nearest safe interval and uses JUMP (O(1) on all devices). Debug seek (simulator only) replays from t=0 for arbitrary precision.

## Key Files

| File | Role |
|------|------|
| `controller/elemctl/service.py` | Core service logic: command handlers, compilation, device lifecycle |
| `controller/elemctl/server.py` | UDS socket server, client management, tick loop |
| `controller/elemctl/controller.py` | Python controller state machine (mirrors C++ SimController) |
| `controller/elemctl/network_device.py` | TCP/UDP device transport (lazy connect, optimistic state) |
| `controller/elemctl/config.py` | Config loading and validation |
| `controller/elemctl/config_edit.py` | Atomic config mutations (add/edit/remove device) |
| `controller/elemctl/discovery.py` | UDP HELLO packet parsing and matching |
| `controller/elemctl/clock_sync.py` | NTP-like sync: probe, filter, correct |
| `controller/elemctl/udp_receiver.py` | Shared UDP socket, per-device-id frame routing |
| `controller/elemctl/wire.py` | Wire protocol encode/decode |
| `controller/elemctl/uds_wire.py` | UDS protocol encode/decode |
| `controller/elemctl/library.py` | Program catalog, metadata extraction, artifact cache |
| `controller/elemctl/tui.py` | Interactive terminal UI (writer client) |
| `controller/elemctl/run.py` | Standalone playback runner (no persistent service) |
| `controller/elemctl/sim.py` | Launcher for network_sim processes |
| `compiler/elements/dsl.py` | User-facing DSL functions |
| `compiler/elements/compiler.py` | Full compilation pipeline |
| `compiler/elements/blob.py` | Binary blob serialization/deserialization |
| `compiler/elements/types.py` | Shared types and constants |

## Entry Point

```bash
./elemctl server --config config.json   # start controller service
./elemctl tui                           # connect TUI as writer
./elemctl sim sim-1 --config config.json # launch a simulator
./elemctl web                           # start web relay (observer)
./elemctl run program.py                # standalone: compile + play
```

`elemctl` is a self-bootstrapping script that creates/maintains a managed venv at `~/.elements/venv`.
