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
- Browser viewer:
  - `elemctl web` serves a live observer page
  - browser receives snapshots, events, and frames in real time
  - TUI can control playback while the browser watches
  - one panel per sim target (physical target rendering, not per logical strip)
  - sim targets only — non-sim targets do not appear on the simulator page
  - mirrored sim targets display the same logical pixels in separate panels
  - frame ingestion decoupled from painting via requestAnimationFrame
  - `device_type` in session target metadata for browser-side filtering
  - connectivity initialized from snapshot, updated live via `device_status`
- 2D sim layout:
  - per-device CSV layouts at `~/.config/elemctl/layouts/<device_uid>.csv`
  - web relay loads and validates layouts at startup, attaches to snapshot
  - browser renders sim devices as 2D shapes from CSV grid
  - devices without a layout show a "no layout" indicator
  - 1-based CSV indices mapped to 0-based logical RGB slices
  - empty cells visually distinct from black LED cells
- Runtime integration hardening:
  - fail-fast guard for conflicting runtime processes
  - explicit per-test discovery ports
  - child-owned TCP port selection for `network_sim`
- Topology-backed strip lengths in controller compile paths:
  - `strip("main")` can resolve configured length
  - shorter programs on longer configured targets leave the tail dark
- `MAX_DEVICE_PIXELS = 250` enforced in config, service, and TUI validation

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
- Layout files are sim-only — they describe how the browser draws simulated devices; real ESP devices have physical layout and don't send frames back for browser rendering
- CSV remains the rendering source of truth for layouts
  - the viewer and renderer only read CSV-derived grid data
  - editor metadata (primitive definitions) lives in a separate sidecar file

## Completed milestones

### Milestone 1: Named-routing targeted loads backend

### Milestone 2: TUI target-selection flow

### Milestone 3: Scene loading

### Milestone 4: Physical-target web renderer

### Milestone 5: 2D sim layout (viewing)

## Next milestone

### Milestone 6: Browser layout editor

**Goal:** Add a visual click-to-place editor in the browser for creating and editing per-device CSV layout files, with drawing primitives for common LED form factors.

**Why this is next:**
- layouts currently require manual CSV editing, which is tedious and error-prone
- the CSV format has proven stable through Milestone 5
- a visual editor makes the 2D layout feature practically usable

#### Frontend stack

- `Vue 3 + TypeScript + Vite`
- Source lives under `controller/web_ui/`
- Vite build output targets `controller/elemctl/web_static/`
- `elemctl web` continues serving static files from `web_static/`
- No runtime Node.js dependency for normal `elemctl web`
- Development can use a separate Vite dev server later; it is not required for the first round

#### Architectural split

- Vue owns the web app shell:
  - viewer layout
  - panel buttons
  - modal/editor UI
  - toolbar/forms/error state
- Plain TypeScript owns the correctness-critical editor and rendering logic:
  - layout document model
  - occupancy/collision
  - viewport math
  - rows/meta serialization
  - canvas rendering helpers
- The current vanilla viewer should not coexist with a Vue editor
  - migrate the viewer into Vue first, then build the editor on top of that foundation

#### Persistence model

- Relay-owned save via `POST /api/layouts/<device_uid>`:
  - browser sends `{"rows": [...], "editor": {...}}`
  - relay validates the grid, converts rows to CSV, writes `<device_uid>.csv` + `<device_uid>.meta.json`
  - relay updates in-memory layout state and broadcasts updated snapshot to all browsers
  - save request includes `base_csv_hash` for future conflict detection (ignored in v1, last-write-wins)
- Relay-owned read via `GET /api/layouts/<device_uid>`:
  - returns `{"rows": [...], "editor": {...}}` or `{"rows": [...], "editor": null}` for hand-edited CSVs
  - known sim device with no layout: 404
  - unknown or non-sim device: 400 error
- CSV/sidecar drift handling:
  - meta.json stores a `csv_hash` (SHA-256 of the canonical CSV generated by the relay)
  - on load, if the hash doesn't match the current CSV, editor metadata is discarded
  - the editor falls back to loading the grid as unstructured single placements

#### Meta JSON format

```json
{
  "version": 1,
  "csv_hash": "sha256 of canonical CSV at save time",
  "primitives": [
    {"type": "single", "index": 7, "position": [3, 5]},
    {"type": "line", "startIndex": 1, "count": 6, "spacing": 1, "start": [2, 0], "end": [2, 10]},
    {"type": "circle", "startIndex": 21, "count": 40, "radius": 12, "center": [20, 20]}
  ]
}
```

All coordinates are bounding-box-relative (adjusted on save when the grid is trimmed).

#### Editor shell

- Per-device modal editor launched from the sim target panel
  - "Create layout" on panels with no layout
  - "Edit layout" on panels with an existing layout
- Near-full viewport modal with dimmed backdrop
- 512×512 virtual grid with dim gray crossing gridlines
  - sparse editor state — not a dense bitmap
- Zoom via scroll wheel (cursor-centered)
  - min: grid visible; max: index numbers readable on cells
  - cap zoom-out when cells become too small to distinguish
- Pan via space+drag or middle-click drag
- Save, Reset, Cancel:
  - Save validates and persists; blocked if invalid (error shown inline)
  - Reset reverts to last saved state (or empty for new layouts)
  - Cancel closes without saving

#### Current index model

- Prominent display: `< N > / max` with skip controls
- Each placement consumes indices starting from the current index
- `>` skips an index, creating an inactive LED (gap)
- Placement blocked if it would exceed device length
- After undo or delete: current index resets to one past the highest placed index
- Live "placed / total" count

#### Drawing tools

**Single LED:**
- Click an empty cell to place the current index
- One click, one LED, current index advances

**Line:**
- Spacing input in toolbar (default: 0 = adjacent cells)
- Click start cell, guidance line with dimmed pixels follows mouse at arbitrary angle
- Direction determined by start point and cursor position
- LED count consumed shown live in preview
- Left click to confirm, ESC to abort
- Spacing N = N empty cells between each LED
- Preview clipped to canvas bounds
- Blocked on overlap with existing cells
- Degenerate case (start == end): treated as single LED placement, saved as single primitive

**Circle:**
- LED count input in toolbar
- Default radius: `ceil(N / (2π) × 1.8)` — minimum where all N LEDs map to distinct grid cells
- Preview follows mouse (center snapped to grid), showing dimmed pixels at computed positions
- +/- keys adjust radius, stepping only through valid radii (all N points on distinct cells)
- Left click to confirm, ESC to abort
- Blocked on overlap; collision shows "not possible" hint
- Minimum LED count: 3

#### LED cell rendering

- Placed LEDs shown as inset rectangles with visible separation (not edge-to-edge)
- Uniform accent color for all placed cells in v1
- Index numbers displayed when zoomed in, hidden when zoomed out
- Empty grid cells visually distinct from placed cells

#### Interaction rules

- No two LEDs may occupy the same grid cell (overlap always blocked)
- Switching tools cancels any in-progress operation (same as ESC)
- Undo at primitive granularity: removes the last placed primitive, frees its cells and indices
- Final CSV = bounding box of placed cells (trimmed on save)

#### Round structure

**Round 0: Vue/TS/Vite foundation + viewer migration**
- Vite/Vue/TS scaffold under `controller/web_ui/`
- Build output wired into `controller/elemctl/web_static/`
- Current browser viewer migrated into Vue with no new product behavior
- Existing viewer behaviors preserved:
  - WebSocket reconnect
  - snapshot/session/device_status handling
  - sim-only target panels
  - 2D sim layout rendering
  - rAF-based frame painting
  - connectivity and empty states
- Viewer math/canvas helpers extracted into plain TypeScript modules

**Round 1: Relay API + editor infrastructure + single LED placement + save**
- POST/GET endpoints on relay
- Grid-to-CSV serialization (bounding box trim)
- Meta JSON read/write with csv_hash drift check
- Modal editor shell with grid, gridlines, zoom, pan
- Current index controls
- Single LED click-to-place
- Undo
- Save/Reset/Cancel

**Round 2: Line primitive**
- Line tool UI with spacing input
- Click-start / guidance-line / click-end flow
- Arbitrary angle, grid-snapped preview
- Overlap detection
- Consecutive index assignment

**Round 3: Circle primitive**
- Circle tool UI with LED count input
- Auto-radius computation with +/- valid radius stepping
- Grid-snapped preview following mouse
- Overlap detection
- Consecutive index assignment

**Round 4: Phase 1 primitive editing**
- Single LED: drag to move to an empty cell
- Any primitive: delete (frees cells and indices)
- Primitive selection indicator (highlight all cells in the primitive)

**Round 5: Phase 2 primitive editing**
- Line: edit spacing, angle, or start position
- Circle: edit radius or center position
- Shared "temporarily remove self from grid, recompute, check collisions, preview, confirm or revert" pattern
- Built once as a generic operation; each primitive type provides `recompute(newParams) → newCells`

#### Not in this milestone

- Cross-device shared canvas
- Pre-load inactive-LED validation
- Browser-side playback controls
- Raw CSV text editing in the editor

## Backlog

These are valid future items, but they are not the immediate next implementation target:

- Web UI modal-shell cleanup:
  - extract shared modal chrome styles used by device/edit/remove modals
  - keep future modal additions from duplicating the same backdrop/header/footer CSS
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

1. Milestone 6: Browser layout editor (Round 0 first)
2. Then backlog prioritization based on real usage
