# Sim viewer: web-driven ring16 simulation

Goal: load and play an animation on a simulated 16-LED ring and **see it
render in the web app**, with playback driven from the browser (the TUI is
deprecated — do not revive it). This doc carries the background, the files to
read, the detailed plan for the first round, and a summary of the rounds after.

Branch: `sim-viewer`.

## Background

Elements is a beat-synced LED animation engine: a C++ runtime (`src/`), a
Python compiler (`compiler/`), and a Python controller + Vue 3 web UI
(`controller/`). The v3 stack is already built and healthy — the compiler emits
`BLOB_VERSION = 3`; the controller (`hub.py`, `session.py`, `service.py`)
handles discover / register / load / play / pause / resume / stop; the C++
runtime decodes and plays v3 blobs. Tests pass when no live runtime is up (an
integration guard fails the suite if `elemctl server`/`sim` are already
running — that is expected, not a real failure).

The intended local setup is one sim device: `instance/config.json` defines
`{device_uid: "sim-16", strip_id: "ring16", length: 16}`, with
`./elemctl server` and `./elemctl sim sim-16` running.

### The data-flow fact that everything below depends on

The v2 → v3 wire change reshaped controller state, and the web viewer was
**not** migrated. In v3:

- `session.strips` is a list of `{strip_id, length}` — there is **no**
  `strip.targets` array.
- devices are a **separate** list, each `{uid, configured, status, strip_id,
  length, label}` — there is **no** `device_type` (sim-ness = the `sim-` uid
  prefix), and **no** `connected` field (use `status === 'online'`).
- the controller sends **full state** snapshots, not incremental device events.

`web.py` folds the controller's `state`/`catalog` messages into one browser
snapshot and passes `session` through verbatim, so the browser sees exactly the
v3 shape above. The fix for round 1 is therefore **frontend-only** — no
controller, protocol, or device changes.

## Files to read first

Controller / state shape:
- `controller/elemctl/service.py` — `_state_dict()` builds the v3 session +
  device state (the authoritative shape).
- `controller/elemctl/web.py` — `_apply_json_message()` / `_send_controller_cmd()`
  (the relay, and the transient-writer path that device-edit APIs already use;
  round 2 reuses it for playback).
- `controller/elemctl/controller_client.py` — used by the round-1 smoke script.

Frontend (the bug locus and the renderer):
- `controller/web_ui/src/composables/useServerState.ts` — `rebuildTargets()`
  still reads `session.strips[].targets[]` and filters on `device_type`
  (both v2); `getDeviceConnected` / `handleDeviceStatus` are dead in v3.
- `controller/web_ui/src/lib/viewerRenderer.ts` — `SimTarget`, `measureLayout`,
  `paintTargets` (canvas painting; keys frame slices off `logicalIndex`).
- `controller/web_ui/src/pages/ViewerPage.vue` — reads `session.playback_state`
  (v3 is `session.state`).
- `controller/web_ui/src/pages/StatusPage.vue` — already on v3 (`uid`,
  `status`, `isSimUid`); good reference for the correct field usage.
- `controller/web_ui/src/lib/editorModel.test.ts` — test conventions (vitest);
  confirm the test script in `controller/web_ui/package.json`.

Content:
- `animations/ring8_blue_wave.py` — the template for the ring16 animation; it
  declares `strip("ring8")` and works verbatim with the name swapped to
  `ring16` (confirmed it compiles to a valid blob at length 16).
- `controller/elemctl/sim_layout.py` — CSV layout format + circle-primitive
  expansion (the layout editor's circle tool produces a ring CSV).

## Round 1 — fix the web viewer's v3 state model (+ ring16 content)

**Why:** the viewer builds its draw list from the empty-in-v3
`session.strips[].targets[]`, so it shows *"No simulated targets"* even while
frames stream; `ViewerPage` reads `session.playback_state`, so playback always
shows *"idle."* The simulation looks broken when it isn't. This is the hidden
blocker — nothing renders until it's fixed.

Tasks:

1. **New pure helper** `controller/web_ui/src/lib/viewerModel.ts` exporting
   `deriveSimTargets(devices, strips, layouts): SimTarget[]`. Use local
   structural input types (do not import from `useServerState`; avoid circular
   deps). For each strip at index `i`, select devices where
   `device.strip_id === strip.strip_id` **and** `device.uid.startsWith('sim-')`;
   emit one `SimTarget` per match:
   - `deviceUid: device.uid`
   - `stripName: strip.strip_id`
   - `logicalIndex: i` — must stay the strip's index in `session.strips`; frame
     slices are keyed by it in `paintTargets`/`ingestFrame`.
   - `logicalLength: strip.length`, `physicalLength: device.length`
   - `connected: device.status === 'online'`
   - `layout: layouts[device.uid] ?? null`, grid sizes via `measureLayout`
   - `canvas: null`, `ctx: null`

2. **Unit test** `controller/web_ui/src/lib/viewerModel.test.ts` (in this round,
   not later): a v3 snapshot with `sim-16` / `ring16` / length 16 and
   `layouts['sim-16']` ⇒ exactly one `SimTarget`, `logicalIndex 0`, layout
   attached, `connected` derived from `status`.

3. **Rewire `useServerState.ts`:** change `SessionStrip` to `{strip_id?, length?}`
   (delete `SessionStripTarget`); `rebuildTargets()` calls
   `deriveSimTargets(getSnapshotDevices(), session.strips ?? [], getSnapshotLayouts())`.
   `ingestFrame` needs no change (already slices by `strip.length`).

4. **Remove dead v2 code:** `getDeviceConnected`, and the `device_status` event
   branch + `handleDeviceStatus` (v3 never emits incremental device events).

5. **`ViewerPage.vue`:** `session.value?.playback_state` → `session.value?.state`.

6. **Content:** `animations/ring16_blue_wave.py` declaring `strip("ring16")`
   (copy `ring8_blue_wave.py`, swap the name).

7. **Local layout (untracked):** create `instance/layouts/sim-16.csv` — a
   16-LED ring grid. **Decision: local-only.** `instance/layouts/` is
   gitignored, so this is runtime state, not repo content; no tracked fixture
   this round. Easiest: draw it once with the web layout editor's circle tool,
   or seed the CSV by hand.

8. **Smoke:** run the frontend build in `controller/web_ui` first — `elemctl web`
   serves the committed `dist/`, so an unbuilt change won't show. Then, with
   `server` + `sim-16` running, drive load+play via a **one-off Python
   `ControllerClient` writer** (`role=writer`, `hello`, then `load`, then
   `play`) — **not curl**; web playback endpoints don't exist until round 2.
   Open the viewer: the ring animates, the pill is green, Playback = `playing`.

**Acceptance:** the ring animates on `sim-16` in the browser and the status
reflects real session state; `viewerModel.test.ts` passes.

Optional, cheap, worth doing alongside: a `ControllerService` compile/load
backend test against `DeviceConfig('sim-16', 'ring16', 16)`.

## Rounds after (summary, with the key details)

**Round 2 — backend playback endpoints.** Add HTTP routes in `web.py` that call
the existing `_send_controller_cmd` (the transient `role=writer` connection the
device-edit APIs already use — no single-writer lock, so this is safe):
`POST /api/programs/rescan`, `POST /api/programs/:programId/load`,
`POST /api/session/play|pause|resume|stop`. The verbs already exist in
`service.py`; this is pure wiring. Also add `programs` to the `SnapshotEvent`
TS type — `web.py` already folds the catalog into `snapshot['programs']`, but
the type doesn't declare it.

**Round 3 — compact web control UI.** Program picker from `snapshot.programs`,
load + transport (play/pause/resume/stop) buttons, inline command errors.
Reflect playback state from the **observer snapshot** (`session.state`,
`cursor_us`, `duration`), not from optimistic local state — each HTTP write is
an independent transient connection. This is the point the web app fully
replaces the TUI for an operator.

**Round 4 — TUI deprecation cleanup.** Once web has parity, retire `tui.py` /
`test_tui.py` and update the docs (`docs/web_app.md` still says playback is
TUI-driven). Tracked separately so it never blocks round 3.

**Later (out of scope, noted so it isn't accidentally half-built):**
- **`loop`:** the frontend has leftover `loop` handling and the old TUI took a
  loop flag, but the **v3 service has no loop semantics**. Do not expose a loop
  toggle until it's plumbed through `service.py` + the session.
- **Offline `strip_render` CLI** (`drafts/TODO.md`): headless, clockless frame
  dump for repeatable/automated simulation and golden tests.
- **Seek / jump** on compiler-marked safe intervals (`drafts/jump.md`).
- **ESP32 hardware** bring-up (`drafts/TODO.md`).

## Wait for confirmation

Do not start writing code until the user confirms. Read the files above, then
confirm the round-1 scope before implementing.
