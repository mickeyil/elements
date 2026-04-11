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
  - `_t0`
  - `tick_once()`
  - `current_t_rel()`
- buffer ownership:
  - `_rgb_storage`
  - `_strip`
- rendering orchestration:
  - `_engine->tick(t_rel)` in the current source
  - redesign below changes this to `Engine::render_frame(t_rel, Strip&)`

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
class Playback {
public:
    explicit Playback(SyncedClock& clock);
    Playback(uint16_t strip_length, SyncedClock& clock);

    bool has_hardware_profile() const;
    const HardwareProfile& hardware_profile() const;
    bool apply_hardware_profile(const HardwareProfile& profile);

    bool handle_load(const uint8_t* blob, size_t blob_len, uint16_t gen);
    void handle_start(int64_t t0_us);
    void handle_jump(int64_t t0_us, float t_rel, uint16_t gen);
    void handle_pause();
    void handle_resume(int64_t t0_us);
    void handle_stop();
    void reset_for_detach();
    void present_black_frame();

    bool tick_once();

    DeviceState state() const;
    float duration() const;
    float current_t_rel() const;
    uint16_t strip_length() const;
    bool requires_sync() const;
    Strip& strip();
    const Strip& strip() const;

private:
    int64_t now_us() const;
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
    int64_t _t0_us = 0;
    float _paused_t_rel = 0.0f;
    bool _requires_sync = false;
};
```

This class is concrete:

- no `virtual output_frame()`
- no `virtual send_telemetry()`
- no `virtual now_mono()`
- no `virtual playback_t0()`

The owner reads the buffer and decides what to do with it.

Important render-pipeline boundary:

- `Engine` owns `Compositor` internally
- `Engine::render_frame(t_rel, Strip&)` advances playback state and renders the
  final RGB frame
- `Playback` does not need compositor details
- gamma correction and hardware channel order are outside the playback core

### Strip-size limit and integer widths

The draft direction keeps the maximum strip length at 1000 LEDs.

Pixel positions, strip lengths, physical LED indices, layer buffer lengths, and
per-buffer HSVA sizes therefore use `uint16_t`.

`Strip` owns exact-sized RGB storage allocated from
`HardwareProfile::strip_length`, so the 1000 LED ceiling does not force the
common small-strip case to preallocate a 1000-pixel RGB frame.

## Core Playback Rules

### 1. Artifact carries sync requirement

The loaded artifact should contain:

```cpp
bool requires_sync;
```

This is a property of the animation/program, not of the play command.

Meaning:

- `requires_sync == true`
  - animation must run against controller-synchronized time
- `requires_sync == false`
  - animation runs against local monotonic time

### 2. `Playback` selects clock domain from `_requires_sync`

The central helper is:

```cpp
int64_t Playback::now_us() const
{
    return _requires_sync ? _clock.now_synced_us()
                          : _clock.now_unsynced_us();
}
```

That is the one intended branch.

This is explicit enough to grep and reason about, while keeping
`tick_once()` / `current_t_rel()` simple.

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
- `handle_jump()` if the new segment is meant to run immediately

This removes the current undesired state where synced content starts before
sync is ready and later falls back / adapts awkwardly.

### 4. Time-domain semantics

The time domains must stay explicit:

- `now_synced_us()`
  - disciplined monotonic time in the controller's time domain
- `now_unsynced_us()`
  - device-local monotonic time

For synced playback:

```cpp
float t_rel = float(_clock.now_synced_us() - _t0_us) / 1e6f;
```

For unsynced playback:

```cpp
float t_rel = float(_clock.now_unsynced_us() - _t0_us) / 1e6f;
```

So:

- synced `_t0_us` is stored in controller time domain
- unsynced `_t0_us` is anchored from local monotonic time

## `SyncedClock`

`Playback` depends on one concrete `SyncedClock`.

Expected API:

```cpp
class SyncedClock {
public:
    bool is_synced() const;
    int64_t now_synced_us() const;
    int64_t now_unsynced_us() const;

    void apply_correction(int64_t offset_us);
    void clear_sync();
};
```

Notes:

- `SyncedClock` is one concrete class, not a class hierarchy.
- simulation and firmware should share the same `SyncedClock` behavior.
- only the raw monotonic source underneath should differ:
  - firmware: `esp_timer_get_time()`
  - desktop sim: `steady_clock`
- `ManualClock` is separate test/tooling utility if needed; it is not a
  production runtime abstraction that `Playback` must be built around.

### Intended behavior

`SyncedClock` should:

- expose `is_synced()`
- publish disciplined monotonic synced time
- publish raw unsynced monotonic time
- absorb small corrections
- never move backward while synced
- transition to unsynced when correction/drift exceeds the allowed band

### Correction ownership

Today:

- `ControllerConnection` receives `CMD_SYNC_RESULT`
- `ControllerConnection` calls `PlaybackDevice::handle_sync_result()`

Redesign direction:

- `ControllerConnection` receives `CMD_SYNC_RESULT`
- `ControllerConnection` feeds `SyncedClock` directly
- `Playback` no longer exposes `handle_sync_result()` / `clear_sync()`

That is an explicit ownership change, not just an implementation detail.

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
    _playback.tick_once();
    apply_gamma(_playback.strip());
    _playback.strip().copy_to(
        reinterpret_cast<uint8_t*>(g_leds),
        _playback.hardware_profile().color_order
    );

    if (_playback.strip_length() < kMaxStripPixels) {
        memset(g_leds + _playback.strip_length(), 0,
               (kMaxStripPixels - _playback.strip_length()) * sizeof(CRGB));
    }
    FastLED.show();
}
```

This is intentionally simple. Presentation signaling can be optimized later if
needed.

### Telemetry / logging

Today state transitions call `send_telemetry()` inside the playback core.

In the redesign, that responsibility moves to the owner:

- firmware may log state changes to serial
- simulation may queue them
- tests may ignore them or inspect state directly

Possible owner-side pattern:

```cpp
DeviceState before = _playback.state();
_playback.handle_start(t0);
DeviceState after = _playback.state();
if (after != before) {
    log_state_change(after, _playback.current_t_rel());
}
```

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

Offline render becomes straightforward:

```cpp
ManualClock clock;
Playback playback(clock);

playback.apply_hardware_profile(HardwareProfile(strip_length));
playback.handle_load(blob.data(), blob.size(), 1);
clock.set_us(0);
playback.handle_start(0);

for (;;) {
    clock.set_us(next_time);
    if (!playback.tick_once())
        break;
    fwrite(playback.strip().bytes(), 1, playback.strip().byte_size(), stdout);
}
```

This is one of the clearest benefits of the redesign.

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
  - `playback_t0()`
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

With a concrete `Playback`, the simplest firmware loop is:

```cpp
_playback.tick_once();
present_buffer();
```

Pros:

- trivial
- no extra API design
- aligned with "simplify first"

Cons:

- may re-present identical pixels while paused/loaded

Current recommendation:

- start with unconditional presentation if needed for simplicity
- only add explicit `frame_emitted` / `buffer_changed` signaling later if ESP
  code actually feels the cost

### 2. State-change signaling

Without virtual telemetry hooks, owners need to detect state changes
explicitly.

That is slightly more work, but it also makes the playback core more honest:
it owns state transitions, not output sinks.

### 3. `tick_once()` contract

Current `PlaybackDevice::tick_once()` returns a bool whose meaning is tied to
"keep ticking / not ended yet", not "new frame rendered". That contract is not
ideal for a concrete `Playback`, but it does not block the redesign.

First-pass recommendation:

- keep a simple bool contract if that reduces churn
- do not overdesign a result object up front
- revisit only if owner code becomes awkward

### 4. Direction C may become less urgent

A concrete `Playback` already captures much of what "separate rendering
pipeline from playback state machine" was trying to achieve:

- no subclassing for RenderDevice
- no subclassing for tests
- no output virtuals in the core

That does not eliminate the value of a future standalone renderer API, but it
does reduce the urgency.

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
8. Prefer simple owner logic over overdesigned signaling APIs.

## Open Decisions

Only a few decisions still look important at this level:

### 1. Sync threshold / hysteresis

`SyncedClock::is_synced()` needs a configured notion of "good enough".

Example starting point:

- threshold around `50ms`

This is tuning, not architecture, but it should be explicit.

### 2. Small-correction policy

When a synced correction would otherwise move time backward:

- hold flat briefly
- jump forward only
- maybe later slew

Current lean:

- simple first
- no backward motion

### 3. Loss-of-sync lifecycle policy

Current lean:

- `SyncedClock` reports loss of sync
- higher-level playback/firmware policy fades out to black
- no silent fallback to unsynced playback for synced content

### 4. Whether/when to add explicit presentation signaling

Current lean:

- do not block the redesign on this
- start with simple owner logic
- add signaling only if it is actually needed

## Bottom Line

The most up-to-date intended abstraction is:

- `Playback` is a concrete playback-and-rendering core
- `SyncedClock` is a concrete time source abstraction
- owners handle output, transport, telemetry, and simulator tooling
- simulation follows firmware semantics
- simplification wins over preserving old wrapper hierarchies

If this works, the codebase gets a clearer center:

`Playback` renders frames. The rest of the system decides what to do with them.
