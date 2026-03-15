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

- Allow duplicate `strip_id` values in config.
- Devices sharing a `strip_id` must also share the same configured `length`.
- Keep routing **named** and config-driven:
  - program strips are matched to devices by `strip_id`
  - the load command chooses which devices participate
- Extend `load_program` with an optional `targets` field rather than adding a second load command.
- If `targets` is omitted, `load_program` should load to all configured devices whose `strip_id` is used by the program.
- If `targets` is provided, only that selected subset should participate in the session.
- Compilation should stay logical-topology-based.
- For selected targets:
  - target length must be `>=` program strip length
  - longer targets are allowed; the tail stays dark naturally
  - shorter targets are rejected immediately
- Raw source `load` stays unchanged for now.
- Scene loading should come **after** named-routing single-program loads work.
- Scene v1 should use one global session/timeline, not multiple independent concurrent playback clocks.

## Active milestone

### Milestone 1: Named-routing targeted loads backend

**Goal:** Allow one logical strip program to be sent to one or more physical devices simultaneously, for example routing `main` to both `sim-144` and `esp-144` in the same synchronized session.

**Command shape target:**

```json
{
  "cmd": "load_program",
  "program_id": "blue_wave",
  "targets": ["sim-144", "esp-144"],
  "loop": true
}
```

#### Round 1: Config/model change

- Relax config validation to allow duplicate `strip_id` values.
- Add a new validation rule:
  - all devices sharing a `strip_id` must have the same configured `length`
- Remove or adapt runtime assumptions that reject duplicate `strip_id` during config/topology preparation.
- Add tests for:
  - duplicate same-length `strip_id` accepted
  - duplicate different-length `strip_id` rejected

#### Round 2: Compile/topology handling

- Update topology-backed strip-length resolution so duplicate `strip_id` groups do not collide incorrectly.
- Derive compile-time logical strip lengths from validated same-length `strip_id` groups.
- Preserve current artifact-cache behavior.
- Add tests for:
  - `strip("main")` resolution with mirrored devices
  - topology fingerprint behavior with duplicate `strip_id` groups

#### Round 3: Targeted load end to end

- Extend `load_program` with optional `targets`.
- Define command semantics:
  - if `targets` is omitted, load to all configured devices whose `strip_id` is used by the program
  - if `targets` is provided, load only to that subset
- Validate:
  - `program_id` exists
  - every target exists
  - no duplicate target ids in the request
  - every program strip is covered by at least one selected device with matching `strip_id`
  - selected devices whose `strip_id` is unused by the program are rejected
  - selected target length is `>=` program strip length
- Make the runtime session operate only on selected targets.
- Keep unselected devices idle.
- Fan out each compiled strip blob to all selected devices whose `strip_id` matches it.
- Add tests for:
  - single-strip mirrored load to sim + esp
  - multi-strip mirrored load across left/right pairs
  - missing-coverage rejection
  - duplicate target rejection
  - shorter-target rejection

#### Round 4: Target-aware observer metadata

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

#### Round 5: Docs and cleanup

- Update `docs/controller.md` with:
  - duplicate `strip_id` semantics
  - mirrored named-routing load semantics
  - `load_program(..., targets=[...])`
- Update roadmap/task references if implementation details shifted during the backend work.

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

- Submit single-strip target selections via `load_program(..., targets=[...])`.
- Show inline validation errors for:
  - no selected targets
  - ineligible targets
  - controller-side binding rejection
- Preserve the existing reply-aware modal behavior.

#### Round 3: Multi-strip program target-selection UI

- Add a follow-up selection view for multi-strip programs:
  - devices grouped by `strip_id`
  - selection remains device-based, routing remains named/config-driven
- Add TUI tests for mirrored single-strip and multi-strip target-selection flows.

## Later milestone

### Milestone 3: Scene loading

**Goal:** Load multiple program/target groups at once for larger installations.

**Recommended scene shape:**

```json
{
  "loop": true,
  "entries": [
    {
      "program_id": "blue_wave",
      "targets": ["sim-144", "esp-144"]
    },
    {
      "program_id": "other_piece",
      "targets": ["esp-left", "esp-right"]
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
  - each entry target list is valid
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

Do not jump to scenes before named-routing single-program loads work end to end.
