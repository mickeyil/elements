# Playback

Working draft for replacing `PlaybackDevice` with a concrete `Playback` class.
See `src/playback_device.{h,cpp}` for current behavior.

## Goal

`Playback` is a synchronous state machine that renders animation frames into a
canonical RGB `Strip`. It owns the state machine, the decoded program, the
engine, and the strip buffer.

It does **not** own transport, hardware output, simulator frame queues,
telemetry sinks, or raw sync offsets. Those are the caller's job.

## Design Direction

The concrete API is in `drafts/playback.h`; the implementation sketch is in
`drafts/playback.cpp`.

Key properties:

- no virtual output, telemetry, or clock hooks
- one concrete `SyncedClock&` injected at construction
- `Engine` owns `Compositor` internally; `Playback` does not touch render
  internals
- timing vocabulary: see `redesign_basic_ds.md#time-naming-policy`
- strip length and integer widths (1000-LED cap, `uint16_t`): see
  `redesign_basic_ds.md` / `hardware_profile.h`

## Core Rules

### 1. `requires_sync` is a property of the artifact

The loaded blob carries `requires_sync: bool`. `true` → the program runs
against remote time; `false` → against local monotonic time. This is a
property of the animation, not of the play command.

### 2. Clock-domain selection

`Playback` keeps one branch: `_requires_sync` picks `now_remote_us()` vs
`now_local_us()`. There is no `_use_synced` latch; `_requires_sync` is the
single source of truth.

### 3. Start admission

A start / resume / jump for a synced program is rejected unless
`SyncedClock::is_synced()` is true. This eliminates the current misbehavior
where synced content starts before sync is ready and later adapts awkwardly.

### 4. Program-time monotonicity

`SyncedClock` is not responsible for making remote time monotonic. The
rendered-animation invariant belongs to `Playback`, because only `Playback`
knows when a START / RESUME / JUMP intentionally re-anchors the timeline.

Invariant: during one continuous playback segment, `Playback` never feeds a
decreasing `t_program` to `Engine::render_frame()`. It enforces this with
`_t_program_cursor_us` — the minimum accepted program position.

- synced segment: `_program_start_us` is anchored in remote time
- unsynced segment: anchored in local monotonic time
- `current_t_program()` reports the cursor; it does not read the clock
- `render_next_frame()` is the only path that samples the clock

### 5. Command lifecycle

| Command                                  | Valid from        | Effect                                                                                       | New state | Result          |
|------------------------------------------|-------------------|----------------------------------------------------------------------------------------------|-----------|-----------------|
| `handle_start(start_us)`                 | `LOADED`, `ENDED` | sets `_program_start_us`; cursor := 0; engine reset if `ENDED`                               | `PLAYING` | —               |
| `handle_pause()`                         | `PLAYING`         | preserves cursor                                                                             | `PAUSED`  | —               |
| `handle_resume(start_us)`                | `PAUSED`          | sets `_program_start_us`; preserves cursor                                                   | `PLAYING` | —               |
| `handle_jump(t, gen)`                    | `LOADED`, `PAUSED`| target strictly ahead of cursor; engine reset; cursor := target; `_program_start_us` untouched | `PAUSED`  | `Unchanged` (§6)|
| `handle_stop()`                          | any non-`IDLE`    | engine reset; strip cleared; timing cleared                                                  | `LOADED`  | `Rendered`      |
| `reset_for_detach()` / load failure / `apply_hardware_profile()` | any               | unload program; clear timing; clear buffer                                                   | `IDLE`    | —               |
| Natural end (inside `render_next_frame()`) | `PLAYING`       | strip cleared; cursor := duration                                                            | `ENDED`   | `Ended`         |

Start / resume / jump for synced programs are rejected unless
`SyncedClock::is_synced()` is true (§3).

### 6. JUMP semantics

JUMP is the explicit timeline discontinuity, intended for rejoin / reconstruct.

- admitted only from `LOADED` or `PAUSED`
- target must be strictly ahead of `_t_program_cursor_us`
- sets the cursor to the target, resets the engine
- does **not** touch `_program_start_us`, does **not** render
- returns `Unchanged`, transitions to `PAUSED`
- a later `handle_resume(program_start_us)` re-anchors and resumes

`render_next_frame()` sees the cursor running ahead of the clock-derived
`t_program` and returns `Unchanged` until the clock catches up:

```
wall clock ───────────────────────────────────────────────→

JUMP(T)              RESUME               [clock reaches T]
│                    │                    │
│ cursor := T        │ start_us pinned    │
│ PAUSED             │ PLAYING            │ render begins
▼                    ▼                    ▼

cursor             T═══════════════════════════════════════
clock-derived    ·····•·····•·····•·········T·····T+Δ······
                     Unchanged              Rendered
```

## `SyncedClock` Integration

`Playback` depends on a concrete `SyncedClock`. Contract and sync policy live
in `drafts/synced_clock.h` and `drafts/synced_clock.md`.

Playback-side boundary:

- admission uses `is_synced()`
- `Playback` owns program-time monotonicity via `_t_program_cursor_us`
- sync-correction ownership is **not** on `Playback`; the
  controller-connection layer feeds `SyncedClock` directly

## Presentation Signaling

`Playback` exposes frame-buffer side effects through `RenderFrameResult`
(defined in `playback.h`):

- `Unchanged` — `_strip` not modified (`IDLE`, `LOADED`, `PAUSED`, `ENDED`,
  no-op commands, pre-start, or cursor-wait)
- `Rendered` — `_strip` holds a newly produced frame
- `Ended` — natural end-of-program: `_strip` cleared to black, state moves
  `PLAYING → ENDED`. Returned exactly once for that transition; later calls
  return `Unchanged`.

Owners present on `Rendered` and `Ended`. Lifecycle queries use `state()`;
there is no parallel `is_active()` accessor.

Methods that can intentionally produce a presentation frame return
`RenderFrameResult`: `render_next_frame()`, `handle_stop()`,
`render_black_frame()`. `handle_jump()` returns the type for API uniformity
but is retime-only and always returns `Unchanged`.

## `render_next_frame()` Contract

`tick_once()` is legacy terminology. The concrete entry point is
`render_next_frame()`.

"Next" is clock- and state-derived, not a fixed frame-index increment. This
is the **only** path that samples the clock and renders. Command handlers do
not render caller-supplied program-time anchors in the first pass; JUMP sets
the cursor and waits for the clock-driven path.

## Ownership Shift

| Responsibility              | Was                                | Now                                        |
|-----------------------------|------------------------------------|--------------------------------------------|
| LED output                  | `ESPDevice::output_frame()`        | firmware owner                             |
| Gamma correction            | playback core                      | owner-owned `GammaCorrection`              |
| Channel reorder             | playback core                      | owner, after rendering                     |
| Telemetry / state-change log| `send_telemetry()` virtual hook    | owner inspects `state()` before/after      |
| Sim frame queueing          | `ESPSimulated::output_frame()`     | sim owner                                  |
| Sync offset storage         | `_sync_offset`, `_sync_valid`      | `SyncedClock`                              |
| Sync correction handling    | `PlaybackDevice::handle_sync_result()` | controller-connection → `SyncedClock`  |
| Raw clock source            | `virtual now_mono()`               | `SyncedClock`, platform `#ifdef`           |
| Start-anchor indirection    | `virtual playback_t0()`            | concrete `_program_start_us`               |

Likely follow-on: `ESPDevice` and `ESPSimulated` shrink to thin owner wrappers
or disappear; `ControllerDevice` / `SimDevice` become hard to justify;
`_gen` / `_frame_index` metadata may move to the owner.

## Firmware / Sim / Offline-render

| Role            | Owns                                                                                 |
|-----------------|--------------------------------------------------------------------------------------|
| Firmware        | `SyncedClock`, `Playback`, `GammaCorrection`, LED output, logging, controller connection, offline/background rotation policy |
| Simulation      | raw monotonic source, `Playback`, frame queueing, sim-side observability             |
| Offline render  | neither `Playback` nor `SyncedClock`; walks `t_program` directly into `Engine::render_frame()` |

### Firmware owner presentation loop

On `render_next_frame()` returning `Rendered` or `Ended`, the firmware owner:

1. applies the current gamma LUT to the strip
2. copies RGB into `g_leds` in the configured channel order
3. zero-pads trailing pixels when the strip is shorter than `kMaxStripPixels`
4. calls `FastLED.show()`
5. on `Ended`, runs the end-of-program hook

A default-constructed `GammaCorrection` is identity; only `RGB` and `BGR`
channel orders are supported in the first pass.

The owner also owns frame pacing. `Playback::target_fps()` exposes the
program's declared cadence; the owner times render + present inside that
budget. The frame budget is not just engine math — gamma/channel conversion,
buffer copy, and `FastLED.show()` all count against it.

The cadence is **not** a LOAD admission gate. Firmware runs the program and
reports cadence/slack telemetry so the composer can lower complexity or the
declared fps. A useful first definition:

```
slack_us = frame_deadline_us - fastled_show_return_us
```

Negative or near-zero slack is the composer-facing signal that the program is
asking too much of the hardware at its declared `target_fps`.

### Offline render

Offline render bypasses `Playback` and `SyncedClock` entirely: decode the
blob, create `Engine`, walk `t_program = frame / target_fps` sequentially
from zero, call `Engine::render_frame()` at each step, write the strip bytes.
The blob's `target_fps` is the default step because it is the cadence the
compiler used for safe-interval filtering; a CLI may expose an explicit
override.

Offline rejects `requires_sync == true` artifacts — offline + synced is an
invalid mode.

## Online / Offline Matrix

|              | **Synced**      | **Unsynced** |
|--------------|-----------------|--------------|
| **Online**   | ✓               | ✓            |
| **Offline**  | ✗ (rejected)    | ✓            |

Controller-side provisioning should reject synced artifacts for offline use;
firmware should verify `requires_sync == false` before starting stored
offline content.

## Open Decisions

### Loss-of-sync lifecycle

Current lean:

- `SyncedClock::is_synced()` flips false automatically when the lease expires
- higher-level playback/firmware policy fades out to black
- no silent fallback to unsynced playback for synced content
- recovery on lease return (e.g. controller-issued JUMP to the nearest safe
  interval) is not yet pinned down; lives with controller/firmware policy,
  not with `SyncedClock`

Sync lease acceptance, freshness, and large-correction policy are controller
concerns — see `drafts/synced_clock.md`.

## Bottom Line

- `Playback` is a concrete playback-and-rendering core
- `SyncedClock` is a concrete time-source abstraction
- owners handle output, transport, telemetry, and sim tooling
- `strip_render` is a direct render-core tool, not a `Playback` wrapper
- simulation follows firmware semantics
- simplification wins over preserving old wrapper hierarchies

`Playback` renders frames. The rest of the system decides what to do with them.
