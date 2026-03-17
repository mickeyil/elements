# Elements Roadmap

> **Purpose:** This file answers "what's next?" based on the current implementation. Completed historical round-by-round detail is intentionally omitted once shipped.

## Current baseline

The following is already implemented:

- Controller-owned program library and loads:
  - `rescan_programs`
  - `publish_program`
  - `load_program`
  - artifact cache
- Controller-owned device lifecycle:
  - `add_device`
  - `edit_device`
  - `remove_device`
- Named routing:
  - duplicate same-length `strip_id` groups
  - `load_program(..., targets=[...])`
  - mirrored fan-out to multiple physical devices
- Controller session model:
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
- Scene loading:
  - internal scene planning/validation
  - public `load_scene`
  - one global session/timeline
- Browser viewer baseline:
  - `elemctl web` serves a live observer page
  - browser receives snapshots, events, and frames in real time
  - TUI can control playback while the browser watches
- Runtime integration hardening:
  - fail-fast guard for conflicting runtime processes
  - explicit per-test discovery ports
  - child-owned TCP port selection for `network_sim`
- Topology-backed strip lengths in controller compile paths:
  - `strip("main")` can resolve configured length
  - shorter programs on longer configured targets leave the tail dark

## Current architectural decisions

These remain settled unless a later milestone explicitly revisits them:

- Routing stays named and config-driven:
  - program strips match devices by `strip_id`
  - load commands decide which devices participate
- Devices sharing a `strip_id` must share the same configured `length`
- `load_program` owns selective targeting through optional `targets`
- If `targets` is omitted, `load_program` selects all configured devices whose `strip_id` is used by the program
- Selected targets:
  - must exist by `device_uid`
  - must cover every program strip
  - must not include unused strip groups
  - may be longer than the logical strip
  - may not be shorter than the logical strip
- Raw source `load` remains the legacy unique-topology path
- Scene loading stays one global session/timeline in v1
- The browser remains an observer path unless a later milestone adds web-side controls
- Physical installation geometry is not part of the DSL/compiler model
  - animations remain layout-agnostic
  - viewer layout should be introduced separately from animation semantics

## Completed milestones

### Milestone 1: Named-routing targeted loads backend

Completed:
- config/model support for mirrored `strip_id` groups
- logical-topology compile/cache behavior
- targeted `load_program`
- mirrored runtime fan-out
- target-aware observer metadata
- docs cleanup

### Milestone 2: TUI target-selection flow

Completed:
- single-strip target selection in `/programs`
- multi-strip target selection in `/programs`
- source-level strip summaries in the program catalog

### Milestone 3: Scene loading

Completed:
- scene schema and service-side planning
- public `load_scene`
- one-session orchestration
- TUI scene submission via `/scene FILE`

## Next milestone

### Milestone 4: Physical-target web renderer

**Goal:** Make the browser viewer render one visual target per physical device instead of one row per logical strip, while keeping the current controller/frame protocol.

**Why this is next:**
- the current web viewer is still constrained to one 1D row per logical strip
- mirrored devices already exist in session metadata, but the browser only uses them for labels
- this is the shortest path to a better multi-device simulator without changing the controller

#### Round 1: Physical-target renderer refactor

- Split browser logic into:
  - frame ingestion keyed by logical session strips
  - rendering keyed by physical targets
- Keep frame slicing by logical strip order and logical strip length
- Fan out each logical RGB slice to all physical targets listed under that strip's `targets`
- Replace immediate paint-on-WebSocket-message with:
  - shared latest-frame state
  - dirty flag
  - one `requestAnimationFrame` paint loop
- Render one visual row/card per physical target
- Mirrored targets should intentionally display the same logical pixels in separate rows/cards
- Show:
  - `device_uid`
  - connectivity
  - logical rendered prefix length
  - physical target length
- Use the existing `devices[]` connectivity and `session.strips[].targets[]` metadata

#### Round 2: Connectivity wiring and tests

- Wire existing `device_status` / snapshot connectivity data into the per-target renderer so targets visibly reflect connect/disconnect changes
- Keep the current logical-strip viewer assumptions only as internal ingestion logic
- Add web tests for:
  - logical-slice fan-out to mirrored physical targets
  - connectivity updates
  - render-loop state updates

## Planned follow-up

### Milestone 5: 2D layout prototype for the web viewer

**Goal:** Break out of the 1D strip-row presentation using a viewer-owned layout file, without pushing geometry into controller config yet.

#### Round 1: External layout file support

- Add `elemctl web --layout FILE`
- Use a simple layout schema keyed by `device_uid`
- Start with straight segments only, for example:
  - `x`
  - `y`
  - `dir`
  - `pixel_pitch`
- Validate and load the layout in the web relay

#### Round 2: Relay/browser integration

- Attach layout data to the browser-facing snapshot payload
- Do not add new controller protocol messages
- If no layout is present, fall back to Milestone 4's physical-target row renderer

#### Round 3: 2D segment rendering

- Render each target into a shared 2D stage/canvas using the supplied geometry
- Keep physical-target labels and connectivity visible
- Support basic fit-to-layout behavior

## Backlog

These are valid future items, but they are not the immediate next implementation target:

- Richer geometry, if real installations need more than straight segments:
  - per-pixel coordinate maps
  - grouped fixtures/panels
  - transforms such as reverse/rotation
- Web control surface:
  - browser-side program/session controls
  - browser-side scene submission
- Web viewer polish:
  - stronger labeling/overlay controls
  - FPS/frame-index debugging
  - optional 2D layout refinements
- ESP32 parity:
  - make real hardware follow the same discovery/configure/playback model as `network_sim`
- Clock sync implementation:
  - move the documented custom sync protocol from design into code
- Audio-player integration:
  - implement the documented controller ↔ audio contract
- Optional `render.py` support for topology-backed implicit strip lengths
- Optional reconsideration of config identity after more real-world use
- Optional controller-owned layout model:
  - only after the web layout schema has proven itself stable in real use

## What is next right now?

If asked "what's next?" and no newer decision has superseded this file, the answer is:

1. Milestone 4: physical-target web renderer
2. then Milestone 5: 2D layout prototype
3. then backlog prioritization based on real usage
