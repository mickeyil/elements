# Playback Redesign

> Working draft for a more concrete replacement of `PlaybackDevice`.
> This document is intentionally biased toward simplifying firmware first.
> Simulation should follow firmware semantics, not the other way around.

## Goal

Replace `PlaybackDevice` with a concrete `Playback` class whose job is:

- own the playback state machine
- own the decoded program / engine / strip buffer
- advance playback time
- render into a canonical RGB `Strip`

`Playback` should not:

- own transport concerns
- own hardware output
- own simulator frame queues
- own serial/network telemetry sinks
- own raw sync offsets

In short:

`Playback` is a synchronous state machine that renders animation frames into a
canonical RGB `Strip`. Everything else is the caller's job.

## Current Facts In Code

Current `PlaybackDevice` in [src/playback_device.h](/home/mickey/dev/elements/src/playback_device.h)
and [src/playback_device.cpp](/home/mickey/dev/elements/src/playback_device.cpp)
already contains almost all playback behavior:

- state machine:
  - `IDLE -> LOADED -> PLAYING <-> PAUSED -> ENDED`
- program lifecycle:
  - `handle_load()`
  - engine ownership
  - strip binding
- playback timing:
  - `_program_start_us`
  - `tick_once()`
  - `current_t_program()`
- buffer ownership:
  - `_rgb_storage`
  - `_strip`
- rendering orchestration:
  - legacy `_engine->tick(t_rel)` in the current source
  - redesign below changes this to `Engine::render_frame(t_program, Strip&)`

Current subclasses mostly supply the destination for rendered output:

- `ESPDevice`
  - raw clock source
  - copy RGB to `FastLED`
  - serial telemetry
- `ESPSimulated`
  - raw clock source
  - queue frames
  - queue telemetry
  - currently also owns `debug_seek` / `debug_step`
- `RenderDevice`
  - synthetic clock
  - writes frames to stdout

This is the main reason a concrete `Playback` is plausible:
the behavioral center is already in the base class.

## Main Design Direction

The proposed replacement is a concrete `Playback` class with no output
inheritance:

```cpp
enum class RenderFrameResult : uint8_t {
    Unchanged,
    Rendered,
    Ended,
};

class Playback {
public:
    explicit Playback(SyncedClock& clock);
    Playback(uint16_t strip_length, SyncedClock& clock);

    bool has_hardware_profile() const;
    const HardwareProfile& hardware_profile() const;
    bool apply_hardware_profile(const HardwareProfile& profile);

    bool handle_load(const uint8_t* blob, size_t blob_len, uint16_t gen);
    void handle_start(int64_t program_start_us);
    RenderFrameResult handle_jump(float t_program, uint16_t gen);
    void handle_pause();
    void handle_resume(int64_t program_start_us);
    RenderFrameResult handle_stop();
    void reset_for_detach();
    RenderFrameResult render_black_frame();

    RenderFrameResult render_next_frame();

    DeviceState state() const;
    float duration() const;
    // Returns 0 when no program is loaded.
    uint8_t target_fps() const;
    float current_t_program() const;
    uint16_t strip_length() const;
    bool requires_sync() const;
    Strip& strip();
    const Strip& strip() const;

private:
    int64_t program_clock_now_us() const;
    void unload_program_();
    void reset_program_state_();
    void reset_timing_state_();
    void clear_render_buffer_();

    SyncedClock& _clock;
    // Strip owns exact-sized RGB storage allocated from HardwareProfile.
    Strip _strip;
    std::unique_ptr<Engine> _engine;
    HardwareProfile _profile{};
    DeviceState _state = DeviceState::IDLE;
    float _duration = 0.0f;
    uint8_t _target_fps = 0;
    int64_t _program_start_us = 0;
    int64_t _t_program_cursor_us = 0;
    bool _requires_sync = false;
};
```

This class is concrete:

- no `virtual output_frame()`
- no `virtual send_telemetry()`
- no `virtual now_mono()`
- no legacy `playback_t0()` virtual playback-start-anchor hook

The owner reads the buffer and decides what to do with it.

Important render-pipeline boundary:

- `Engine` owns `Compositor` internally
- `Engine::render_frame(t_program, Strip&)` advances playback state and
  renders the final RGB frame
- `Playback` does not need compositor details
- gamma correction and hardware channel order are outside the playback core
- gamma correction is represented by a caller-owned `GammaCorrection` LUT

### Strip-size limit and integer widths

The draft direction keeps the maximum strip length at 1000 LEDs.

Pixel positions, strip lengths, physical LED indices, PixelView sizes, and
per-buffer HSVA sizes therefore use `uint16_t`.

`Strip` owns exact-sized RGB storage allocated from
`HardwareProfile::strip_length`, so the 1000 LED ceiling does not force the
common small-strip case to preallocate a 1000-pixel RGB frame.

`HardwareProfile` may also carry last-mile output facts such as channel order
and desired gamma. `Playback` only needs the strip length for buffer allocation.
The owner applies gamma/channel-order policy after rendering.

The current output profile intentionally supports only `RGB` and `BGR` channel
order. Other layouts can be added later as localized changes if hardware needs
them.

### Time naming policy

The canonical timing vocabulary is defined once in
`redesign_basic_ds.md#time-naming-policy`. Playback follows that policy:

- loose program-relative seconds are `t_program`
- animation-relative seconds are `t_animation`
- absolute program start anchors are `program_start_us`
- selected-clock absolute now is `program_clock_now_us()`
- remote/local clock domains are exposed through `SyncedClock`
- new implementation code should not introduce `t_rel`, `t0`,
  `now_synced_us()`, `now_unsynced_us()`, or `now_controller_us()`

Frame/reporting/protocol names such as `DeviceFrame::t_program` and
start/resume `program_start_us` are part of the intended follow-up
implementation rename pass; the live source may still use legacy names until
that pass lands.

## Core Playback Rules

### 1. Artifact carries sync requirement

The loaded artifact should contain:

```cpp
bool requires_sync;
```

This is a property of the animation/program, not of the play command.

Meaning:

- `requires_sync == true`
  - animation must run against remote-synchronized time
- `requires_sync == false`
  - animation runs against local monotonic time

### 2. `Playback` selects clock domain from `_requires_sync`

The central helper is:

```cpp
int64_t Playback::program_clock_now_us() const
{
    return _requires_sync ? _clock.now_remote_us()
                          : _clock.now_local_us();
}
```

That is the one intended branch.

This is explicit enough to grep and reason about, while keeping
`render_next_frame()` and `current_t_program()` simple.

There is no additional `_use_synced` latch in the current design direction.
The intent is that `_requires_sync` is enough, with assertions/tests catching
invalid transitions.

### 3. Start admission rule

For synced content:

```cpp
if (_requires_sync && !_clock.is_synced()) {
    return;
}
```

That same rule applies to:

- `handle_start()`
- `handle_resume()`
- `handle_jump()` when the loaded program requires remote sync

This removes the current undesired state where synced content starts before
sync is ready and later falls back / adapts awkwardly.

### 4. Time-domain semantics

The time domains must stay explicit:

- `now_remote_us()`
  - remote-domain time estimate derived from the accepted sync offset
- `now_local_us()`
  - device-local monotonic time

For synced playback:

```cpp
float t_program = float(_clock.now_remote_us() - _program_start_us) / 1e6f;
```

For unsynced playback:

```cpp
float t_program = float(_clock.now_local_us() - _program_start_us) / 1e6f;
```

So:

- synced `_program_start_us` is stored in remote time domain
- unsynced `_program_start_us` is anchored from local monotonic time

### 5. Accepted program-time monotonicity

`SyncedClock` is not responsible for making remote time monotonic after
every correction. The render invariant lives in `Playback`:

- during one continuous playback segment, `Playback` must not feed a decreasing
  `t_program` to `Engine::render_frame()`
- `_program_start_us` stores the selected-clock anchor for the current segment
- `_t_program_cursor_us` stores the minimum accepted program position in integer
  microseconds
- `current_t_program()` reports the cursor; it does not read the clock or mutate
  playback state. After a future JUMP, this may be ahead of the last presented
  frame because the device is deliberately waiting for the clock to catch up.

`render_next_frame()` is the clock-derived render path. It computes:

```cpp
int64_t t_program_us = program_clock_now_us() - _program_start_us;
```

Then it ignores negative values for delayed starts. If the clock-derived value
is below `_t_program_cursor_us`, it returns `RenderFrameResult::Unchanged`
instead of clamping upward and rendering. This covers both small backward clock
corrections during ordinary playback and deliberate future JUMP targets during
live rejoin. Once the clock reaches or passes the cursor, `render_next_frame()`
advances the cursor to the sampled clock value and renders.

`handle_jump()` is the explicit timeline discontinuity. It resets the engine
and sets `_t_program_cursor_us` to the target, but it does not set
`_program_start_us` and does not render a frame. JUMP is for rejoin/reconstruct
work. It is admitted only from `LOADED` or `PAUSED`, and only when the target is
strictly ahead of the current cursor. Playback resumes from a later
`handle_resume(program_start_us)`.

Timing-state rules:

- `handle_start(program_start_us)`
  - sets `_program_start_us = program_start_us`
  - seeds `_t_program_cursor_us = 0`
- `handle_pause()`
  - changes state only
  - preserves `_t_program_cursor_us`
- `handle_resume(program_start_us)`
  - sets `_program_start_us = program_start_us`
  - preserves `_t_program_cursor_us`
- `handle_jump(t_program, gen)`
  - is valid only from `LOADED` or `PAUSED`
  - rejects targets that are not strictly ahead of `_t_program_cursor_us`
  - resets the engine
  - sets `_t_program_cursor_us` to the target
  - does not touch `_program_start_us`
  - returns `Unchanged`
  - moves to `PAUSED`
- `handle_stop()`, load/unload failure, and detach reset
  - clear timing state
- natural end-of-program
  - clears `_strip` to black
  - sets state to `ENDED`
  - reports `current_t_program() == duration()`

## `SyncedClock`

`Playback` depends on one concrete `SyncedClock`. The class is defined in
`drafts/synced_clock.h`; sync policy, wire format, and controller-side
integration are covered in `drafts/synced_clock.md`.

Contract relevant to `Playback`:

- `is_synced()` — true iff the current sync lease is valid.
- `now_remote_us()` — remote-domain estimate in microseconds. Not guaranteed
  monotonic across sync updates.
- `now_local_us()` — local monotonic time in microseconds.
- Sync corrections are delivered as leases (`offset_us`, `valid_for_us`);
  `SyncedClock::is_synced()` flips to false automatically when the lease
  expires.

Correction ownership moves off `Playback`: the firmware controller-connection
layer feeds `SyncedClock` directly, and `Playback` no longer exposes
`handle_sync_result()` / `clear_sync()`.

`SyncedClock` does not enforce the rendered-animation monotonicity invariant.
That belongs to `Playback`, because only `Playback` knows when a start,
resume, jump, stop, or restart intentionally re-anchors the program timeline.
It is enforced through `_t_program_cursor_us`.

## What Moves Out Of The Core

### Hardware output

Today `ESPDevice::output_frame()` does:

- copy `rgb_data()` into `g_leds`
- zero trailing pixels
- `FastLED.show()`

In the redesign, that becomes firmware owner logic instead of virtual override
logic.

Example:

```cpp
void FirmwareApp::tick_playback_()
{
    const RenderFrameResult result = _playback.render_next_frame();
    if (result == RenderFrameResult::Unchanged) {
        return;
    }

    apply_gamma(_playback.strip(), _gamma);
    _playback.strip().copy_to(
        reinterpret_cast<uint8_t*>(g_leds),
        _playback.hardware_profile().color_order
    );

    if (_playback.strip_length() < kMaxStripPixels) {
        memset(g_leds + _playback.strip_length(), 0,
               (kMaxStripPixels - _playback.strip_length()) * sizeof(CRGB));
    }
    FastLED.show();

    if (result == RenderFrameResult::Ended) {
        handle_program_ended();
    }
}
```

The firmware owner initializes `_gamma` when applying its output profile:

```cpp
_gamma.set_gamma(profile.gamma);
_playback.apply_hardware_profile(profile);
```

If `profile.gamma` is invalid, `GammaCorrection::set_gamma()` leaves the
previous valid LUT unchanged. A default constructed `GammaCorrection` is already
identity, which is the intended sim/raw-output behavior.

Presentation is driven by `RenderFrameResult`: owners present on `Rendered` and
`Ended`, and do nothing for `Unchanged`.

Owners also own frame pacing. After LOAD, `Playback::target_fps()` exposes the
program's intended presentation cadence from the blob. Firmware should pace
render/present cycles to that cadence and keep the last-mile LED update inside
the same frame budget. The budget is not just engine math: gamma/channel
conversion, copying into the FastLED buffer, and `FastLED.show()` time all
count. The exact `FastLED.show()` estimate can be filled in during
implementation, when the real output path is measured.

The cadence is not a LOAD capability gate. Firmware should run the program,
measure whether it is meeting the target, and report actual cadence/slack
telemetry so the composer can reduce animation complexity or lower the declared
fps when the budget is too tight.

### Telemetry / logging

Today state transitions call `send_telemetry()` inside the playback core.

In the redesign, that responsibility moves to the owner:

- firmware may log state changes to serial
- simulation may queue them
- tests may ignore them or inspect state directly

Possible owner-side pattern:

```cpp
DeviceState before = _playback.state();
_playback.handle_start(program_start_us);
DeviceState after = _playback.state();
if (after != before) {
    log_state_change(after, _playback.current_t_program());
}
```

Cadence telemetry follows the same ownership rule. The firmware loop knows the
frame deadline, when render math finished, and when `FastLED.show()` returned,
so it reports actual fps and slack from the owner instead of pushing those
hooks into `Playback`. A useful first definition is:

```text
slack_us = frame_deadline_us - fastled_show_return_us
```

Negative or near-zero slack is the composer-facing signal that the program is
asking too much of the hardware at its declared `target_fps`.

The exact "clean telemetry" gate is a tuning decision for controller/authoring
tooling, not a LOAD admission rule. A later implementation should define the
required slack margin and observation window before a program is considered
production-ready.

This is slightly more explicit, but simpler overall than virtual telemetry
hooks in the playback core.

### Simulator frame queueing

Today `ESPSimulated` queues `SimRgbFrame` in `output_frame()`.

In the redesign, simulation should adapt to the concrete playback model.
That means:

- playback semantics are defined by firmware simplicity first
- sim owns whatever frame queueing/aggregation it needs
- no simulator-only feature should shape the first-pass playback abstraction

This is especially important for `debug_seek`, which remains out of scope for
the first redesign pass.

## Firmware / Sim / Render Ownership

### Firmware

Firmware owns:

- `SyncedClock`
- `Playback`
- `GammaCorrection`
- output to LEDs
- logging / telemetry
- controller connection
- offline/background rotation policy

### Simulation

Simulation owns:

- raw monotonic source for `SyncedClock`
- `Playback`
- frame queueing / aggregation
- any simulator-side observability

Simulation should adapt to the playback contract; it should not force the
playback core to preserve extra simulator-only behavior.

### Offline render

Offline render is a direct render-core caller. It does not need `Playback` or a
clock because it already owns the frame schedule and can pass explicit
program-relative times into `Engine`.

```cpp
DecodeError err = DecodeError::Ok;
Program* program = decode_program(blob.data(), blob.size(), strip_length, &err);
if (program == nullptr) {
    return error(decode_error_name(err));
}

if (program->requires_sync) {
    free_program(program);
    return error("offline render requires an unsynced artifact");
}

const float duration = program->duration;
const uint8_t fps = program->target_fps;
Engine* engine = Engine::create(program);  // takes ownership of program
if (engine == nullptr) {
    return error("engine alloc failed");
}

Strip strip;
if (!strip.resize(strip_length)) {
    delete engine;
    return error("strip alloc failed");
}

for (int64_t frame = 0;; frame++) {
    const float t_program = float(frame) / float(fps);
    if (t_program >= duration) {
        break;
    }
    if (engine->render_frame(t_program, strip)) {
        fwrite(strip.bytes(), 1, strip.byte_size(), stdout);
    }
}

delete engine;
```

This is one of the clearest benefits of separating the render core from the
playback state machine. Offline render walks `t_program` sequentially from `0`,
using the blob's `target_fps` as its default frame step, so it does not need a
deterministic playback clock. A tooling CLI may expose an explicit override,
but the artifact's cadence is the default because it is the cadence the
compiler used for safe-interval filtering. Offline render also rejects
`requires_sync` artifacts because offline + synced is an invalid mode.

`ManualClock` is therefore not a `Playback` constructor argument. Deterministic
`Playback` tests should eventually use a manual raw-time seam behind concrete
`SyncedClock`; offline render bypasses `Playback` and drives `Engine` directly.

## What Disappears

If this direction is adopted, the following can leave the playback core:

- output inheritance:
  - `output_frame()`
  - `send_telemetry()`
  - `clear_queued_runtime_outputs()`
- sync ownership:
  - `_sync_offset`
  - `_sync_valid`
  - `_playback_uses_sync`
  - `handle_sync_result()`
  - `clear_sync()`
- timing indirection:
  - `playback_t0()` virtual playback-start-anchor
- raw clock virtual:
  - `now_mono()`
- render-output policy:
  - gamma correction
  - channel reordering

Likely follow-on simplifications:

- `ESPDevice` may disappear as a class
- `ESPSimulated` may disappear or shrink into a very thin owner/wrapper
- `ControllerDevice` / `SimDevice` become much less justified
- `_gen` / `_frame_index` become candidates to move out of the playback core
  if the owner becomes responsible for emitted-frame metadata

## What Does Not Disappear

Responsibilities move; they do not vanish.

Still needed somewhere:

- hardware profile management
- LED output
- gamma correction
- channel reordering
- state-change logging / telemetry
- sim frame aggregation
- background provisioning / offline playback policy

The simplification is about ownership and clarity, not about deleting those
needs from the system.

## Important Tradeoffs

### 1. Presentation signaling

`Playback` exposes frame-buffer side effects through `RenderFrameResult`:

```cpp
enum class RenderFrameResult : uint8_t {
    Unchanged,
    Rendered,
    Ended,
};
```

Meaning:

- `Unchanged`
  - `_strip` was not modified by this call
  - returned for `IDLE`, `LOADED`, `PAUSED`, already `ENDED`, invalid/no-op
    commands, and delayed start while `t_program < 0`
- `Rendered`
  - `_strip` contains a newly produced frame
- `Ended`
  - natural end-of-program transition
  - `_strip` is cleared to black
  - state changed `PLAYING -> ENDED`
  - returned exactly once for that transition; later calls return `Unchanged`

Owners present on `Rendered` and `Ended`. Lifecycle queries use `state()`; no
parallel `is_active()` style accessor is planned.

Public methods that can intentionally produce a presentation frame return
`RenderFrameResult`. That includes `render_next_frame()`, `handle_stop()`, and
`render_black_frame()`. `handle_jump()` returns `RenderFrameResult` for API
uniformity, but it is retime-only and returns `Unchanged`. Lifecycle/reset
methods may clear internal stale buffers without implying presentation.

### 2. State-change signaling

Without virtual telemetry hooks, owners need to detect state changes
explicitly.

That is slightly more work, but it also makes the playback core more honest:
it owns state transitions, not output sinks.

### 3. `render_next_frame()` contract

`tick_once()` is legacy terminology from `PlaybackDevice`. The concrete
`Playback` entry point is `render_next_frame()`.

"Next" means the next frame accepted by `Playback` for presentation from the
current state and selected clock, not a fixed frame-index increment. This call
is the only path that derives a new `t_program` from the clock and renders it.
Command handlers do not render caller-supplied program-time anchors in the first
pass; JUMP sets the cursor and waits for the clock-driven path.

### 4. Direct render-core tooling

A concrete `Playback` already captures much of what "separate rendering
pipeline from playback state machine" was trying to achieve for firmware,
simulation, and state-machine tests:

- no subclassing for playback tests
- no output virtuals in the core

That does not make `strip_render` a `Playback` caller. The offline renderer is
the concrete case where direct render-core access is the better fit:
`decode_program -> Engine::create -> Engine::render_frame(t_program, strip)`.
It exercises rendering without threading clock or lifecycle state through a tool
that does not need those behaviors.

## Online / Offline Matrix

This redesign assumes two orthogonal axes:

- timing requirement:
  - synced
  - unsynced
- delivery mode:
  - online
  - offline

Valid combinations:

- online + synced
- online + unsynced
- offline + unsynced

Invalid combination:

- offline + synced

That is why:

- controller-side provisioning should reject synced artifacts for offline use
- firmware should verify `requires_sync == false` before starting stored
  offline content

## Recommended First Pass

If the goal is to simplify aggressively while keeping the redesign grounded,
the first pass should do this:

1. Replace artifact timebase enum language with `requires_sync: bool`.
2. Introduce concrete `SyncedClock`.
3. Move sync correction ownership from `PlaybackDevice` to `SyncedClock`.
4. Replace `PlaybackDevice` with concrete `Playback`.
5. Move hardware output and telemetry to owners.
6. Keep sim on the same playback rules as firmware.
7. Keep `debug_seek` out of scope.
8. Use `RenderFrameResult` for owner presentation decisions.
9. Make `strip_render` a direct render-core tool, not a `Playback` wrapper.

## Open Decisions

### 1. Loss-of-sync lifecycle policy

Current lean:

- `SyncedClock::is_synced()` flips false automatically when the lease expires.
- higher-level playback/firmware policy fades out to black.
- no silent fallback to unsynced playback for synced content.
- recovery policy when the lease returns mid-playback (e.g. controller-issued
  `JUMP` to the nearest safe interval) is not yet pinned down; it lives with
  controller/firmware policy, not with `SyncedClock`.

Sync lease acceptance, freshness, and large-correction policy are controller
concerns. See `drafts/synced_clock.md`.

## Settled Test/Tool Time Seam

Decision:

- keep production `SyncedClock` concrete
- do not add virtual/callback clock hooks to the production API
- deterministic `Playback` tests need a manual raw-time seam, but this draft
  source does not implement it yet
- do not use this test seam for `strip_render`; offline rendering should pass
  explicit `t_program` values directly to `Engine`

That preserves the concrete production shape while still allowing monotonicity,
freshness, and correction-policy tests to control time without sleeping.

## Bottom Line

The most up-to-date intended abstraction is:

- `Playback` is a concrete playback-and-rendering core
- `SyncedClock` is a concrete time source abstraction
- owners handle output, transport, telemetry, and simulator tooling
- `strip_render` is a direct render-core tool
- simulation follows firmware semantics
- simplification wins over preserving old wrapper hierarchies

If this works, the codebase gets a clearer center:

`Playback` renders frames. The rest of the system decides what to do with them.
