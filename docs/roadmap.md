# Elements Roadmap

> **Purpose:** This file is the durable planning source for "what's next?" questions. It records the current completed baseline, the active architectural decisions, and the next milestones broken into implementation rounds.

## Current baseline

The following work is already in place:

- Controller-owned program library and loads:
  - `rescan_programs`
  - `publish_program`
  - `load_program`
  - artifact cache
- Controller-owned device lifecycle:
  - `add_device`
  - `edit_device`
  - `remove_device`
- TUI as a thin controller client:
  - `/devices`
  - `/newdevice`
  - `/programs`
  - `/session`
  - `/seek`
- Empty installations are supported.
- Browser viewer baseline exists:
  - `elemctl web` serves a live observer page
  - TUI can control playback while the browser watches frames in real time
- Runtime integration test isolation/hardening:
  - fail-fast guard for conflicting runtime processes
  - explicit per-test discovery ports
  - child-owned TCP port selection for `network_sim`
- Topology-backed strip lengths in DSL compile paths:
  - `strip("main")` can resolve configured length in controller-backed compile paths
  - longer configured strips may accept shorter program strips; the unused tail stays dark
- Reusable sample animation:
  - `animations/blue_wave.py`

## Current architectural decisions

These decisions are settled unless a later milestone explicitly revisits them:

- Keep `strip_id` unique in config for now.
- Do **not** remove the current simple 1:1 `strip_id -> device` model yet.
- Add a new **binding-based load path** for mirrored and selective loads.
- Separate:
  - **logical program strips** used by DSL programs
  - **physical targets** identified by `device_uid`
- Compilation should stay logical-topology-based.
- Binding fan-out should happen after compile, during load.
- For bound targets:
  - target length must be `>=` program strip length
  - longer targets are allowed; the tail stays dark
  - shorter targets are rejected immediately
- Scene loading should come **after** binding-based single-program loads work.
- Scene v1 should use one global session/timeline, not multiple independent concurrent playback clocks.

## Active milestone

### Milestone 1: Binding-based mirrored loads

**Goal:** Allow one logical strip program to be sent to one or more physical devices simultaneously, for example binding `main` to both `sim-144` and `esp-144` in the same synchronized session.

**Command shape target:**

```json
{
  "cmd": "load_program_bindings",
  "program_id": "blue_wave",
  "bindings": {
    "main": ["sim-144", "esp-144"]
  },
  "loop": true
}
```

#### Round 1: Service and protocol shape

- Add a new controller command:
  - `load_program_bindings`
- Define command validation rules:
  - `program_id` must exist
  - every logical strip named in `bindings` must exist in the compiled program
  - every bound `device_uid` must exist in config/runtime inventory
  - every program strip must be bound at least once
  - no physical device may appear in two different logical-strip bindings in one load
  - bound device length must be `>=` program strip length
- Keep existing `load_program` unchanged for the simple 1:1 case.
- Add protocol/docs coverage for the new command.

#### Round 2: Session-specific active target set

- Change the load path so a session is built from the **bound target subset**, not implicitly from all configured devices.
- Prefer a session-specific controller target set over introducing partial-active logic across the entire existing controller.
- Ensure unbound configured devices remain idle.
- Make session end detection, pause/play/seek state, and frame assembly operate only on active targets for the current session.

#### Round 3: Mirrored artifact fan-out

- Compile the program once using logical strips.
- Reuse the same compiled strip artifact for all targets bound to the same logical strip.
- Preserve artifact-cache behavior:
  - cache keys remain based on logical source/topology
  - binding fan-out does not require recompilation
- Add tests for:
  - one logical strip -> two physical targets
  - mixed single-target and mirrored-target bindings
  - longer-target dark-tail behavior
  - shorter-target rejection

#### Round 4: Target-aware session metadata and observer wire shape

- Update controller/session metadata so observers can distinguish:
  - logical strip name
  - physical `device_uid`
  - physical target length
- Update snapshot / `session_start` shape accordingly.
- Update frame/observer-side assumptions as needed so the browser and TUI can reason about mirrored targets explicitly rather than only logical strip order.
- Add compatibility/upgrade tests for:
  - snapshot session metadata
  - `session_start`
  - frame consumer assumptions

#### Round 5: Service-level verification and docs

- Add service tests for:
  - mirrored `load_program_bindings`
  - missing/duplicate/invalid bindings
  - inactive devices staying idle
  - looped mirrored loads
- Update `docs/controller.md` with the binding model and semantics.

## Next milestone

### Milestone 2: TUI target-selection flow

**Goal:** Make mirrored/selective loads usable from the TUI without hand-written JSON.

#### Round 1: Single-strip target selection UI

- Extend the `/programs` load flow for single-strip programs.
- After selecting a program, open a target-selection step that lists eligible devices.
- Allow selecting:
  - one target
  - multiple targets for mirrored loads
- Keep the existing loop toggle in the load flow.

#### Round 2: Validation and submission

- Submit single-strip target selections via `load_program_bindings`.
- Show inline validation errors for:
  - no selected targets
  - ineligible targets
  - controller-side binding rejection
- Preserve the existing reply-aware modal behavior.

#### Round 3: Multi-strip program bindings UI

- Add a follow-up binding view for multi-strip programs:
  - one logical strip at a time
  - assign one or more physical devices per logical strip
- Ensure the UI prevents assigning the same physical device twice within one load.
- Add TUI tests for mirrored single-strip and multi-strip binding flows.

## Later milestone

### Milestone 3: Scene loading

**Goal:** Load multiple programs/bindings at once for larger installations.

**Recommended scene shape:**

```json
{
  "loop": true,
  "entries": [
    {
      "program_id": "blue_wave",
      "bindings": {
        "main": ["sim-144", "esp-144"]
      }
    },
    {
      "program_id": "other_piece",
      "bindings": {
        "left": ["esp-left"],
        "right": ["esp-right"]
      }
    }
  ]
}
```

#### Round 1: Scene format and service command

- Introduce a new command:
  - `load_scene`
- Define and document the scene schema.
- Validate:
  - each entry program exists
  - each entry bindings map is valid
  - no physical target is assigned twice across the whole scene

#### Round 2: Global-session orchestration

- Keep scene v1 within one global session/timeline.
- Require all programs in the scene to have a compatible duration/timeline policy.
- Define how global safe intervals are derived across scene entries.
- Load all entries atomically or fail the scene load cleanly.

#### Round 3: TUI scene workflow

- Add a TUI path for submitting a scene file or scene spec.
- Keep this separate from the simpler `/programs` flow.
- Add tests and docs.

## Backlog

These are valid future items, but they are not the next implementation target:

- Web viewer polish:
  - better strip rendering
  - optional 2D layout prototype
  - layout file design
- Web control-surface work:
  - browser-side program/session controls
- Optional `render.py` support for topology-backed implicit strip lengths
- Optional reconsideration of config identity once the binding model has proven itself

## What is next right now?

If asked "what's next?" and no newer decision has superseded this file, the answer is:

1. **Milestone 1, Round 1**
2. then **Milestone 1, Round 2**
3. then **Milestone 1, Round 3**

Do not jump to scenes before binding-based single-program loads work end to end.
