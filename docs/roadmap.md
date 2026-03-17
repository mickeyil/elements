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
  - layout is a viewer/simulation concern, introduced separately from animation semantics
- `MAX_DEVICE_PIXELS = 250` — to be enforced in core config validation (may later split into per-device-type limits if needed)
- Layout files are sim-only — they describe how the browser draws simulated devices; real ESP devices have physical layout and don't send frames back for browser rendering

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

**Goal:** Make the browser viewer render one visual panel per physical target instead of one row per logical strip, keeping the current controller/frame protocol unchanged. Only sim devices produce renderable frame data; non-sim targets appear as status-only (connected/disconnected).

**Why this is next:**
- the current web viewer is constrained to one 1D row per logical strip
- mirrored devices already exist in session metadata, but the browser only uses them for labels
- this is the shortest path to a better multi-device simulator without changing the controller

#### Round 1: Renderer refactor and physical-target rendering

- Split browser logic into:
  - frame ingestion keyed by logical session strips (unchanged slicing)
  - rendering keyed by physical targets
- Fan out each logical RGB slice to sim targets listed under that strip's `targets[]`
- Mirrored sim targets intentionally display the same logical pixels in separate panels
- Non-sim targets rendered as status-only panels (device_uid, connectivity — no pixel data)
- Replace immediate paint-on-WebSocket-message with:
  - shared latest-frame state
  - dirty flag
  - one `requestAnimationFrame` paint loop
- Each panel shows:
  - `device_uid`
  - connectivity status (wired from `device_status` events and snapshot `devices[]`)
  - for sim targets: logical rendered prefix length vs physical target length

#### Round 2: Tests

- Logical-slice fan-out to mirrored physical targets
- Connectivity updates reflected in per-target rendering
- Render-loop state updates (dirty flag, rAF decoupling)

### Milestone 5: 2D sim layout

**Goal:** Render each sim device as a 2D shape in the browser using per-device CSV layout files, instead of a 1D row.

#### Layout storage

- `~/.config/elemctl/layouts/<device_uid>.csv`
- Web relay scans the layouts directory at startup, matches files to sim `device_uid` values

#### CSV format

- Cells are blank or a 1-based integer
- Indices must be unique
- Indices must be within `1..configured_length`
- Missing indices are allowed — they represent inactive LEDs that stay dark
- Indices do not need to be contiguous (holes are valid for LEDs hidden by physical installation)

Example (8 active LEDs in an L-shape on a 10-LED strip, LEDs 9-10 inactive):
```
1,2,3,4
,,,5
8,7,6,
```

#### Inactive LEDs

- The CSV implicitly defines which LEDs are active (present) and inactive (absent)
- Inactive LEDs are expected to stay dark — the browser can warn if they receive non-black data
- Pre-load rejection of animations targeting inactive LEDs is deferred — the eventual model is: compiler exposes used indices per strip, load-time validation checks them against the layout's active set. For now, the animation author checks the layout visually when choosing pixel groups

#### Round 1: Layout loading and validation

- Web relay reads and validates CSV files from the layouts directory
- Cross-checks against configured device length (indices must be within range)
- Attaches layout data to the snapshot sent to browser clients
- No new controller protocol messages

#### Round 2: 2D per-device rendering

- One panel per sim device
- Devices with a layout: render 2D shape from CSV grid
- Devices without a layout: show "no layout" indicator (no fallback to row rendering)
- Paint from the logical RGB slice using the CSV pixel mapping
- Show device_uid, connectivity, length

#### Round 3: Tests

- CSV parsing and validation (duplicates, out of range, and valid sparse layouts with holes)
- Layout-to-snapshot attachment in the relay
- Browser rendering with and without layout files

#### Not in this milestone

- Click-to-place browser editor for creating/editing CSV files (later)
- Cross-device placement on a shared canvas (later)
- Pre-load inactive-LED validation (later — compiler exposes used indices, load-time checks against layout active set)

## Backlog

These are valid future items, but they are not the immediate next implementation target:

- Click-to-place layout editor in the browser:
  - visual tool for creating/editing per-device CSV layout files
  - should come after the CSV format proves stable through manual use
- Cross-device installation view:
  - combine multiple device panels into one shared 2D canvas
  - requires placement/offset metadata beyond per-device CSVs
- Pre-load inactive-LED validation:
  - compiler exposes used indices per strip
  - load-time validation checks used indices against layout active set
  - rejects animations targeting inactive LEDs before runtime
- Web control surface:
  - browser-side program/session controls
  - browser-side scene submission
- ESP32 parity:
  - make real hardware follow the same discovery/configure/playback model as `network_sim`
- Clock sync implementation:
  - move the documented custom sync protocol from design into code
- Audio-player integration:
  - implement the documented controller ↔ audio contract
- Optional `render.py` support for topology-backed implicit strip lengths
- Optional controller-owned layout model:
  - only after the web layout schema has proven itself stable in real use

## What is next right now?

If asked "what's next?" and no newer decision has superseded this file, the answer is:

1. Add `MAX_DEVICE_PIXELS = 250` to core config validation
2. Milestone 4: physical-target web renderer
3. Milestone 5: 2D sim layout
4. Then backlog prioritization based on real usage
