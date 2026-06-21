# Controller

The controller is a long-running Python service on the base station. It
compiles animations into binary blobs, hands them to the devices, drives
playback, and exposes an API to the web app over a local socket.

## The shape of it

```
┌──────────────────────────────────────────────┐
│                 Base station                  │
│                                               │
│   ┌────────────┐   unix socket  ┌──────────┐ │
│   │ Controller │◄──────────────►│ Web app  │ │
│   └─────┬──────┘                └──────────┘ │
│         │ TCP + UDP                          │
└─────────┼────────────────────────────────────┘
          │ same LAN
    ┌─────┴──────┐
    │  Devices   │
    │ (ESP32 or  │
    │ simulator) │
    └────────────┘
```

The devices connect out to the controller; the controller only listens.
See `protocol.md` for the wire.

## What it does

1. **Compiles** animations from DSL source into one binary blob per
   strip, plus metadata (duration, safe intervals).
2. **Accepts devices.** It listens for each device's `DISCOVER` and
   answers wanted ones with an `OFFER`, then accepts the TCP link they
   open and reads their `REGISTER`.
3. **Routes blobs by `strip_id`.** Each compiled strip goes to every
   device whose config names that `strip_id`; several devices on one
   `strip_id` mirror the same strip.
4. **Drives playback** with LOAD / START / PAUSE / RESUME / STOP across
   the devices in the session.
5. **Assembles previews.** It collects each simulator's frame previews
   and joins them, by moment, into whole-program frames for the web app.
6. **Manages the session** (below).
7. **Owns the config**, and handles add / edit / remove device requests.
8. **Answers clock-sync pings** as a stateless responder; the devices do
   the sync math themselves.
9. **Serves the API** to the web app over a unix socket.

## Config

A JSON file at `instance/config.json`, the source of truth for the
physical setup. `elemctl` creates it on first run with the four default
ports and no devices.

```json
{
  "controller": {
    "discovery_port": 6040,
    "link_port": 6041,
    "frame_port": 6042,
    "sync_port": 6043
  },
  "devices": [
    {
      "device_uid": "sim-1",
      "strip_id": "main",
      "length": 60,
      "label": "front ring"
    }
  ]
}
```

- `device_uid` — the device's only identity (MAC-based for ESP32, chosen
  for simulators; `esp-` / `sim-` prefixes tell them apart).
- `strip_id` — the routing key; matches the `strip()` names in the DSL.
- `length` — pixel count, 1 to 300.
- `label` — optional display name for the UI; falls back to the uid.

The device's address is not in the config: the controller learns it when
the device connects.

## DSL and compiler

Animations are written in a small Python DSL (`compiler/elements/dsl.py`).
See `../docs/dsl_example.py` for a complete program. A program ends with
`build(beat, duration, target_fps=50, requires_sync=False)`.

The compiler (`compiler/elements/compiler.py`) turns that into blobs:

1. **Check** pixel bounds, required parameters, and strip names.
2. **Resolve times** from beats to seconds.
3. **Plan layers** by packing animations that never overlap in time onto
   the same layer.
4. **Plan storage** so animations can share working pixel memory.
5. **Resolve dependencies** between animations that read each other.
6. **Find safe intervals** — the moments it is safe to start the engine
   from cold (used for seek and rejoin; see `jump.md`).
7. **Emit** one blob per strip.

The result is a manifest with each strip's blob, the duration, and the
safe intervals. The blob byte format is `../docs/blob_format.md`.

## Program library

The controller scans an animations directory for `.py` programs, reads
each one's beat and duration, and offers the list to the web app. It
caches compiled blobs by source so it does not recompile unchanged
programs. Publishing a program writes it into the library.

## The session

One session at a time. Loading a program starts a new session
(`session_id`) and resets its `epoch`; the session has a state (idle,
loaded, playing, paused, ended) and a play cursor.

A session survives a device dropping off. The controller tracks each
member separately:

- **attached** — its TCP link is up.
- **serving** — it has the session's blob loaded and is following
  playback commands.

When a device drops, it stays a member of the session but stops being
attached or serving; the show carries on for the others on the
controller's clock. When it comes back, the controller brings it back to
the loaded program. A device that joins or rejoins *after* the show
started waits at the loaded state for now: catching it up mid-show (seek,
live rejoin) is designed but not yet wired up; `jump.md` has it.

## Key files

| File | Role |
|------|------|
| `controller/elemctl/service.py` | command handling, compilation, session driving |
| `controller/elemctl/server.py` | unix-socket server and client handling |
| `controller/elemctl/session.py` | session state and per-member desired state |
| `controller/elemctl/hub.py` | the device-facing side: discovery, link, sync |
| `controller/elemctl/wire.py` | device protocol encode / decode |
| `controller/elemctl/preview.py` | joins per-strip previews into program frames |
| `controller/elemctl/config.py` | config loading and validation |
| `controller/elemctl/config_edit.py` | add / edit / remove a device |
| `controller/elemctl/library.py` | program catalog and compiled-blob cache |
| `controller/elemctl/program_metadata.py` | reads beat / duration from a program |
| `controller/elemctl/controller_protocol.py` | the web-app API protocol |
| `controller/elemctl/controller_client.py` | a client of that API |
| `controller/elemctl/sim_layout.py` | simulator pixel layout files |
| `controller/elemctl/sim.py` | launches and supervises simulator devices |
| `controller/elemctl/web.py` | the web UI server |
| `compiler/elements/dsl.py` | the DSL |
| `compiler/elements/compiler.py` | the compiler |
| `compiler/elements/blob.py` | blob serialization |
| `compiler/elements/types.py` | shared types and constants |

## Running

```bash
./elemctl server          # start the controller
./elemctl web             # start the web UI server
./elemctl sim sim-1       # start a simulator device
```

`elemctl` bootstraps and maintains its own Python environment under
`local/venv`.
