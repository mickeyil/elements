# Time-Naming Policy

Canonical naming conventions for time-related identifiers in the
runtime. `synced_clock.md` and `compiler.md` link here as the source of
truth; runtime semantics for `Playback` live in `src/playback.{h,cpp}`.

## Rules

- loose values, parameters, locals, and returns use strict domain names
- `t_<domain>` means float seconds relative to that domain
- `<thing>_us` / `<thing>_ns` means an absolute timestamp or delta in
  the named units
- delta names encode direction/sign at public API boundaries unless the
  sign convention is pinned nearby (docstring or enclosing type)
- struct fields may use short contextual names when the struct type
  pins meaning and units

## Runtime vocabulary

| Name                                         | Meaning                                                                |
|----------------------------------------------|------------------------------------------------------------------------|
| `t_program`                                  | float seconds relative to program start                                |
| `t_animation`                                | float seconds relative to animation/event start                        |
| `program_start_us`                           | absolute program start in the selected clock domain                    |
| `program_clock_now_us()`                     | absolute now in the selected program clock domain                      |
| `now_remote_us()` / `now_local_us()`         | absolute remote / local-monotonic timestamps                           |
| `is_synced()`                                | status predicate for remote-domain validity                            |
| `apply_sync_offset(offset_us, valid_for_us)` | applies a sync-offset lease; `offset_us` = local minus remote          |
| `render_next_frame()`                        | accepts the next renderable frame from the selected clock and state   |
| `RenderFrameResult`                          | presentation-side result from a playback call that may update `_strip` |
| `current_t_program()`                        | current program-time cursor                                            |
| `_t_program_cursor_us`                       | minimum accepted program position, integer microseconds                |
| `target_fps`                                 | program-level intended presentation cadence, in Hz                     |

## Contextual struct fields

Struct types pin meaning and units, so short names are fine:

| Field                       | Meaning                        |
|-----------------------------|--------------------------------|
| `AnimationEvent::start`     | program-relative float seconds |
| `AnimationEvent::duration`  | float seconds                  |
| `CopyOp::at`                | program-relative float seconds |
| `Program::duration`         | float seconds                  |
| `Program::target_fps`       | intended cadence, Hz           |

## Legacy holdouts

`src/deprecated/network_sim.cpp`, `src/deprecated/sim_controller.{h,cpp}`,
`src/deprecated/controller_device.h`, and `playback_device.h` still carry the v2 names `t_rel` and `t0_us`
(plus `DeviceFrame::t_rel` / `ProgramFrame::t_rel`). These get renamed
during the owner rewire tracked in `TODO.md` § Playback.

## Compiler-side exception

Python compiler code may keep `_sec` suffixes on program-relative times
(`start_sec`, `required_start_sec`, `end_sec`) because it also handles
beat-space values. The runtime vocabulary above still applies to
everything the compiler emits into the blob or wire format.
