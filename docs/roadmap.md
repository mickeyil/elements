# Elements Roadmap

> **Purpose:** This file answers "what is next?" in priority order. Shipped work is kept near the bottom as reference, not as the active implementation target.

## Top priority now

### Milestone A: First real ESP32 end-to-end playback

**Goal:** Get one real ESP32 device working through the normal controller path end-to-end:

- controller recognizes the device
- controller can connect and configure it reliably
- controller can `LOAD` and `PLAY`
- a real LED strip visibly lights under controller control
- disconnect/reconnect is visible and recoverable

**Success boundary for Milestone A:**

- one configured `esp32` device can be reached by the controller
- a simple known-good program can be loaded and played on real LEDs
- basic reconnect / power-cycle recovery works
- logs are good enough to debug failures without guessing

This milestone is about **proving the real hardware path**, not about full browser control parity or advanced synchronization work yet.

## Milestone A rounds

### Round 0: Short audit spike

**Goal:** Pin the exact minimum firmware + controller work before writing code.

**Content:**

- audit the existing protocol/runtime expectations in:
  - [design.md](/home/mickey/dev/elements/docs/design.md)
  - [controller.md](/home/mickey/dev/elements/docs/controller.md)
  - [controller/elemctl](/home/mickey/dev/elements/controller/elemctl)
- confirm what already exists:
  - controller-side `device_type: "esp32"` expectations
  - TCP control protocol shape
  - discovery vs static endpoint assumptions
  - playback/runtime code that can be reused on the ESP32
- identify the minimum missing firmware responsibilities:
  - Wi-Fi
  - TCP server
  - command dispatch
  - playback device wrapper
  - LED output

**Output:**

- one short implementation note
- exact firmware scope for Round 1
- exact controller assumptions for Round 2

This should be brief. It is not meant to become a long analysis-only phase.

### Round 1: ESP32 firmware bring-up

**Goal:** Create the first real firmware that speaks the existing controller protocol and can drive LEDs.

**Content:**

- implement the real ESP32 runtime wrapper:
  - `ESPDevice` or equivalent real-hardware device class
  - monotonic clock using `esp_timer_get_time()`
  - LED output via the chosen hardware library/runtime
- implement networking on the device:
  - Wi-Fi connection
  - TCP server socket on the device
  - command receive / decode / dispatch loop
- make the firmware compatible with the same high-level command set as `network_sim`:
  - configure
  - load
  - start
  - stop
  - pause / resume if close enough
- first smoke test can be firmware-local:
  - hardcoded blob or minimal direct path just to prove the runtime compiles and LEDs move

**Acceptance criteria:**

- firmware builds and runs on the ESP32
- the strip can be driven from the real device runtime
- the device is ready to accept controller commands over TCP

### Round 2: Controller-to-hardware connection path

**Goal:** Make the controller talk to the real ESP32 reliably.

**Content:**

- wire or finish the real-device path for `device_type: "esp32"`
- prefer **static endpoint first**
  - explicit host + tcp_port
  - discovery can come later
- verify controller-side connect/configure lifecycle against the real device
- ensure connection state is reflected cleanly in:
  - controller logs
  - snapshot / status model
  - browser/TUI status views

**Acceptance criteria:**

- one configured ESP32 shows up as connected
- disconnect / reconnect transitions are observable
- no simulator-only assumptions block the real device path

### Round 3: End-to-end `LOAD` / `PLAY`

**Goal:** Prove the full controller-driven playback path on real LEDs.

**Content:**

- verify and fix the real-device implementations of:
  - `LOAD`
  - ACK / error reply
  - `START`
  - `STOP`
- test with one simple known-good program
- ensure controller-side failure handling is clean for:
  - connect failure
  - configure failure
  - load rejection
  - decode failure
  - broken socket

**Acceptance criteria:**

- controller can load a real program to the ESP32
- LEDs visibly play
- stop returns the strip to the expected state
- controller and observer status remain coherent

### Round 4: Hardware observability

**Goal:** Make real-hardware failures diagnosable.

**Content:**

- add or refine controller-side logs for:
  - connect
  - disconnect
  - configure success/failure
  - load success/failure
  - playback command failures
- make sure device identity is obvious in log lines
- add any minimal status information needed during bring-up:
  - last seen
  - host / port if useful
  - clear offline / failed state transitions

**Acceptance criteria:**

- when playback fails, the terminal makes it clear where it failed
- debugging does not require guessing or packet sniffing for normal bring-up issues

### Round 5: Reliable operator loop

**Goal:** Make repeated use on real hardware practical.

**Content:**

- harden reconnect behavior
- test power-cycle / reboot recovery
- verify repeated:
  - load
  - play
  - stop
  - reconnect
- fix stale state cleanup issues as they appear

**Acceptance criteria:**

- controller can recover from ESP32 restart or temporary disconnect
- repeated load/play/stop cycles do not require restarting the controller

## Priority after Milestone A

Once Milestone A is complete, the likely next milestones are:

### Milestone B: Real-hardware diagnostics and operator UX

- better per-device diagnostics
- clearer status surfaces in TUI/browser
- targeted smoke-test flows for hardware
- device registration / provisioning UX
  - keep the full hardware UID as the canonical internal identity
  - allow operators to identify and configure devices by a short human-facing suffix label
    - initial plan: last 3 MAC bytes, e.g. `0A11E3`
  - resolve short IDs to the full UID only when the suffix is unique
  - on the rare suffix collision, fall back to a longer/full UID instead of renaming the device

### Milestone C: Web control parity

- browser-side playback/session controls
- browser-side device/config mutations where appropriate
- keep the browser from being observer-only

### Milestone D: Clock sync implementation

- implement the documented controller-led sync protocol
- move beyond best-effort local timing toward coordinated real-device timing

### Milestone E: Audio-player integration

- implement the documented controller ↔ audio contract
- align audio with real playback/session control

## Active backlog and lower-priority work

These are valid tasks, but they are not the main priority while real hardware bring-up is in progress.

### Browser layout editor polish

- continue tightening:
  - selection clarity
  - drag/reshape affordances
  - context-menu clarity
  - warning copy
  - operator feedback

### Browser layout editor feature backlog

- polyline / multi-segment strip paths
- richer primitive editing
- cross-device installation view
- pre-load inactive-LED validation

### Browser control backlog

- browser-side program/session controls
- browser-side scene submission

### Optional rendering / layout backlog

- optional `render.py` support for topology-backed implicit strip lengths
- optional controller-owned layout model
  - only if the current browser layout schema proves stable in real use

## Settled architectural decisions

These remain settled unless a later milestone explicitly revisits them:

- routing stays named and config-driven
- program strips match devices by `strip_id`
- `load_program` owns selective targeting through optional `targets`
- if `targets` is omitted, `load_program` selects all configured devices whose `strip_id` is used by the program
- selected targets:
  - must exist by `device_uid`
  - must cover every program strip
  - must not include unused strip groups
  - may be longer than the logical strip
  - may not be shorter than the logical strip
- raw source `load` remains the legacy unique-topology path
- scene loading stays one global session/timeline in v1
- the browser remains an observer path until a milestone explicitly adds web-side controls
- physical installation geometry is not part of the DSL/compiler model
- layout files are sim-only
- CSV remains the rendering source of truth for simulated layouts

## Shipped baseline (reference only)

The following already exists and should be treated as platform baseline, not as the current milestone target:

- controller-owned program library and loads:
  - `rescan_programs`
  - `publish_program`
  - `load_program`
  - artifact cache
- controller-owned device lifecycle:
  - `add_device`
  - `edit_device`
  - `remove_device`
- named routing:
  - duplicate same-length `strip_id` groups
  - `load_program(..., targets=[...])`
  - mirrored fan-out to multiple physical devices
- controller session model:
  - active-target sessions
  - target-aware observer metadata
  - loop handling
- TUI as a thin controller client:
  - `/devices`
  - `/newdevice`
  - `/programs`
  - `/session`
  - `/seek`
  - `/scene FILE`
- scene loading:
  - internal scene planning/validation
  - public `load_scene`
  - one global session/timeline
- browser viewer:
  - `elemctl web` serves a live observer page
  - browser receives snapshots, events, and frames in real time
  - TUI can control playback while the browser watches
  - one panel per sim target
  - sim targets only
  - mirrored sim targets display the same logical pixels in separate panels
  - frame ingestion decoupled from painting via requestAnimationFrame
  - connectivity initialized from snapshot, updated live via `device_status`
- 2D sim layout:
  - per-device CSV layouts
  - browser 2D rendering from CSV grid
  - browser layout editor with placement/editing/undo/renumber confirmation
- runtime integration hardening:
  - fail-fast guard for conflicting runtime processes
  - explicit per-test discovery ports
  - child-owned TCP port selection for `network_sim`
- topology-backed strip lengths in controller compile paths
- `MAX_DEVICE_PIXELS = 250` enforcement in config, service, and TUI validation
