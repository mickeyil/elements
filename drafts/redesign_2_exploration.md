# PlaybackDevice Redesign Exploration

> **DRAFT** -- This is a working exploration document, not a finalized design.
> It captures findings, insights, and candidate directions from a code review
> of PlaybackDevice and its derivatives. Read if you need context on the
> playback subsystem redesign; skip if you're working on unrelated areas.

> **Terminology note:** this file is historical context. The current intended
> implementation vocabulary is defined in `redesign_basic_ds.md` and
> `redesign_playback.md`: `SyncedClock`, `now_remote_us()`,
> `now_local_us()`, `program_start_us`, `render_next_frame()`, `t_program`,
> and `t_animation`. Older names still appearing below such as `t_rel`,
> `_t0`, and `playback_t0()` should not guide implementation; earlier
> proposals of `now_synced_us()` / `now_unsynced_us()` and
> `now_controller_us()` are likewise superseded by `now_remote_us()` /
> `now_local_us()`. Older statements that the synced clock itself must
> never move backward are superseded by the current rule: `Playback` owns
> monotonic accepted `t_program` for rendering.

## Summary

PlaybackDevice is the base class for all LED animation playback. It bundles
two distinct responsibilities: (1) a real-time playback state machine
(load/play/pause/resume/jump/stop with timing and sync policy), and (2) the
program rendering pipeline (decode blob, create Engine, bind Strip, tick
through frames). Every subclass inherits both, even when it only needs one.

Clock synchronization logic is spread across four layers -- DiscoveryService
(firmware, UDP probe/response), the Python controller (clock_sync.py computes
and filters offsets; service.py sends corrections and gates ESP rejoin),
ControllerConnection (firmware, receives offset over TCP), and PlaybackDevice
(stores and applies offset at tick time) -- and handled with runtime flags and
fallbacks rather than explicit compile-time or load-time decisions.

A key design premise we are exploring: whether an animation needs clock sync
is knowable at creation time but is not currently encoded in any artifact.
That forces runtime guesswork. Encoding it would enable gating and eliminate
fallback paths that only exist to support undesired states.

The main simplification directions are:

1. **Encode timing requirements in the animation blob** -- a `requires_sync`
   flag set at compile time, so the system knows whether an animation needs
   remote clock sync before it tries to play it.

2. **Inject clock access into PlaybackDevice and replace ad-hoc sync fields
   with a `SyncedClock` abstraction** -- PlaybackDevice should depend on
   explicit clock reads, not own raw sync flags and offset math. Synced
   programs read remote-domain time from `SyncedClock::now_remote_us()`;
   unsynced programs read local monotonic time from
   `SyncedClock::now_local_us()`. The selection is centralized in one
   helper, driven by the loaded program's `requires_sync` flag.

3. **Separate the rendering pipeline from the playback state machine** --
   the Engine/Strip/decode lifecycle is useful on its own (offline rendering,
   testing). It doesn't require the full state machine and shouldn't force
   callers into the PlaybackDevice abstraction.


---

## Detailed Findings

### Current class hierarchy

```
PlaybackDevice (abstract base -- state machine + rendering pipeline)
  +-- ESPDevice          (firmware: FastLED output, ESP32 clock, serial telemetry)
  +-- ESPSimulated       (desktop sim: queues frames/telemetry, debug seek/step)
  +-- RenderDevice       (offline CLI tool: synthetic clock, writes frames to stdout)
  +-- TestDevice(s)      (test mocks with controllable clocks)

ControllerDevice (pure interface -- parallel hierarchy for SimController)
  +-- SimDevice          (adapter: wraps ESPSimulated as ControllerDevice)
  +-- FakeDevice         (test mock)
```

### Finding 1: PlaybackDevice centralizes the state machine

All playback logic (state transitions, timing, engine lifecycle) lives in the
base class. ESPDevice and ESPSimulated override only I/O hooks:

| Virtual              | ESPDevice                  | ESPSimulated               |
|----------------------|----------------------------|----------------------------|
| `now_mono()`         | `esp_timer_get_time()`     | `steady_clock`             |
| `output_frame()`     | memcpy to FastLED + show() | queue SimRgbFrame          |
| `send_telemetry()`   | Serial.printf              | queue SimTelemetry         |
| `playback_t0()`      | local-clock fallback       | base default (pass-through)|
| `clear_queued_...()`   | no-op                    | clear queues               |

No subclass overrides `handle_*` or `tick_once`. The subclasses are I/O
adapters, not behavior variants. The state machine is centralized in one
place, even if later findings suggest the surrounding responsibilities should
be simplified.

### Finding 2: Sync logic is scattered and implicit

**Where sync state lives today:**

- `DiscoveryService` (firmware) -- handles UDP sync probe/response (NTP-style
  timestamp exchange). Doesn't compute offset, just responds with timestamps.
- Controller (Python) -- computes the offset from round-trip timestamps.
- `ControllerConnection` (firmware) -- receives computed offset over TCP,
  validates boot token and sequence, forwards to PlaybackDevice.
- `PlaybackDevice` -- stores `_sync_offset`, `_sync_valid`,
  `_playback_uses_sync`. Uses offset in `tick_once()` and `current_t_rel()`.
- `ESPDevice::playback_t0()` -- fallback: if no sync, manufactures a local
  timebase from `now_mono()`.

**What `_playback_uses_sync` actually protects against:**

The flag snapshots whether sync was active when `_t0` was computed (at
start/resume/jump). Without it, if sync becomes valid mid-playback, the time
formula breaks: `_t0` was calculated in device-local time, but the formula
would start subtracting a sync offset, producing wildly wrong `t_rel` values
(negative or jumped).

**Bug found by Codex review:** `_playback_uses_sync` snapshots the bool but
not the offset value. `_sync_offset` can be mutated at any time by
`handle_sync_result()`. A synced playback segment can still experience a time
jump when a new correction arrives. No test covers this case.

**Asymmetry:** Rejoin of ESP32 devices during playback is gated on sync
readiness (in Python service layer). Initial play, resume, and seek are not
gated. This asymmetry likely contributed to the firmware growing the
local-clock fallback in `ESPDevice::playback_t0`.

### Finding 3: "Which clock does this animation need?" is not encoded anywhere

The animation blob carries duration, layer, and event data. Strip
configuration and safe intervals live in `CompiledManifest`, not in the blob
itself (see `compiler/elements/types.py:125`, `compiler/elements/blob.py:5`).
Neither the blob nor the manifest contains a timing requirement. The same blob
is played synced or local depending on runtime circumstances. The fallback in
`ESPDevice::playback_t0` is the main consequence: it silently adapts, but
produces poor multi-device results.

**Design premise (not current code):** since the timing requirement is
knowable at animation creation time, it could be encoded in the blob as a
simple `requires_sync` flag:

```cpp
bool requires_sync;  // true = needs remote sync, false = runs on local clock
```

This enables a single clear rule: synced programs do not enter playback until
the clock reports `is_synced()`. Unsynced programs do not care about sync at
all. That eliminates the runtime fallback path.

This also separates two orthogonal concepts that were previously conflated:

- **synced / unsynced** -- a timing requirement of the animation itself
- **online / offline** -- a delivery mode
  - **online**: controller sends commands live
  - **offline**: animation is stored on the device and firmware manages
    playback autonomously

These axes combine cleanly:

- online + synced: valid
- online + unsynced: valid
- offline + unsynced: valid
- offline + synced: invalid

That framing explains why offline/background provisioning must reject synced
artifacts: it is not a special-case rule, it is the invalid cell in the
matrix. This is now the intended conceptual model going forward.

**Current design commitment for this exploration:** once admitted, a
remote-synced program runs against a `SyncedClock` whose remote-domain
estimate may change when corrections are accepted. Rendered-animation
monotonicity is enforced by `Playback`, not by the clock (see
`redesign_playback.md`). The correction acceptance policy is still open. This
document is no longer exploring the earlier "translate t0 once, then ignore
sync until the next segment boundary" direction.

**Additional scope constraint for this redesign pass:** `debug_seek` is out of
scope. It is a simulator-only feature that has not yet justified its added
complexity. The first redesign pass should keep the same playback rules for
simulated and real devices, and only reintroduce simulator-specific seek/debug
behavior after there is concrete need.

### Finding 4: RenderDevice doesn't need to be a PlaybackDevice

RenderDevice is a production CLI tool (not a test). It decodes a blob and
steps through frames offline at fixed FPS, writing `t_rel` + RGB per frame to
stdout. Its usage:

```cpp
device.handle_load(blob, len, 1);
device.set_now(0);
device.handle_start(0);
for (...) {
    device.set_now(next_frame_time);
    device.tick_once();
}
```

It follows a straight-line path: IDLE -> LOADED -> PLAYING -> ENDED. It never
pauses, resumes, jumps, syncs, or uses telemetry. What it actually needs is
the rendering pipeline (decode + Engine + Strip), not the state machine.

This suggests PlaybackDevice bundles two separable concerns:

1. **Rendering pipeline**: decode blob into Program, create Engine bound to
   Strip, tick Engine at a given t_rel, read RGB output.
2. **Playback state machine**: lifecycle management (load/play/pause/stop),
   timing (real-time clock, sync-aware t0), frame pacing, telemetry.

If the rendering pipeline were directly accessible (via Engine/Program/Strip),
RenderDevice wouldn't need to inherit from PlaybackDevice at all.

Current decision: `strip_render` / RenderDevice should take that path. It is a
direct render-core tool that decodes the blob, constructs `Engine`, and passes
explicit `t_program` values to `Engine::render_frame()`.

### Finding 5: Clock access should be injected, not inherited

`now_mono()` is a pure virtual on PlaybackDevice, forcing every subclass to
implement it. But the raw time source is a platform concern (ESP timer,
steady clock, synthetic), not a playback behavior variant.

A separate injected clock source would:

- Remove `now_mono()` as a pure virtual
- Let platform code own the clock instance
- Let tests and tools inject controllable clocks without subclassing

The intended runtime shape is simpler than a full clock taxonomy:

- **SyncedClock**: exposes `is_synced()`, `now_remote_us()`, and
  `now_local_us()`. Acceptance, smoothing, large-correction policy, and the
  transition back to unsynced are still open (see `redesign_playback.md`).
  Under the current boundary, `SyncedClock` itself is *not* required to
  publish monotonic remote time -- small accepted corrections may step the
  estimate. `Playback` owns the monotonic accepted `t_program` invariant for
  rendering.
- **Manual raw clock source**: a separate testing/tooling source for
  deterministic `Playback` tests when time is externally driven via `set()`.

`SyncedClock` is intended to be one concrete class, not the root of a class
hierarchy. A future manual raw-time seam should not become another production
runtime clock implementation behind a shared virtual interface, and it should
not be passed to Playback. Playback still receives the concrete device clock.

ESP and simulation should share the same `SyncedClock` behavior. The justified
platform difference is only the raw monotonic source underneath it
(`esp_timer_get_time()` on firmware, `steady_clock` on desktop).

PlaybackDevice should not own raw sync fields or do sync math directly.
Instead, it uses one private helper such as:

```cpp
int64_t now_us() const {
    return _requires_sync ? _clock.now_remote_us()
                          : _clock.now_local_us();
}
```

The branch is explicit and centralized in one place. `tick_once()` and
`current_t_rel()` stay simple, and the call site still clearly declares which
time domain is being used.

The intended flow is:

1. `handle_load()` reads `requires_sync` from the blob and stores it on the
   loaded program/device state.
2. `handle_start()` / `handle_resume()` / `handle_jump()` check `is_synced()`
   if and only if `requires_sync == true`.
3. During playback, one helper uses `_requires_sync` to select
   `now_remote_us()` or `now_local_us()`.

No additional runtime latch such as `_use_synced` is part of the current
direction. The loaded program's `requires_sync` flag is intended to be
sufficient to drive the helper, with assertions/tests/simulation used to catch
incorrect transitions.

The time domains should also be explicit:

- `now_remote_us()` publishes a remote-domain time estimate (may step when
  a correction is accepted; see `redesign_playback.md`)
- `now_local_us()` publishes device-local monotonic time

For synced playback, `_t0` is stored in the remote's time domain and
`t_rel` is computed as:

```cpp
float t_rel = float(_clock.now_remote_us() - _t0_us) / 1e6f;
```

For unsynced playback, `_t0` is anchored from local monotonic time and `t_rel`
is computed against `now_local_us()`.

The sync correction path should also move explicitly:

- today: `ControllerConnection -> PlaybackDevice::handle_sync_result()`
- redesign direction: `ControllerConnection -> SyncedClock`

That is why `handle_sync_result()` / `clear_sync()` are expected to leave
PlaybackDevice's public API.

With the rendering pipeline separated from PlaybackDevice (Finding 4),
RenderDevice and render-core tests do not need a clock at all -- they pass
`t_program` directly to the Engine. Playback tests still need deterministic
time behind the concrete clock because they are testing clock-derived
state-machine behavior, but the exact test seam is intentionally left out of
this draft source sketch.

**Important distinction:** this document no longer assumes a naive
`now_remote_us = mono_now - latest_offset` clock. The useful abstraction is
a `SyncedClock` that exposes a remote-domain estimate; correction acceptance
policy is still open (see `redesign_playback.md`). Earlier claims in this doc
that the clock itself must never publish time that goes backward are
superseded: the monotonic invariant lives on `Playback`'s accepted `t_program`,
not on `SyncedClock`.

### Finding 6: ControllerDevice is a thin parallel hierarchy

ControllerDevice is a pure interface whose command surface closely mirrors
PlaybackDevice's. SimDevice is a pure-delegation adapter. DeviceFrame and
SimRgbFrame are identical structs. ControllerDevice adds `drain_frames()` and
`debug_seek()`/`supports_debug_seek()` over the shared command surface, but
does not mirror PlaybackDevice's profile, sync, or detach APIs
(`apply_hardware_profile`, `handle_sync_result`, `reset_for_detach`, etc.).

This exists so SimController doesn't depend on PlaybackDevice internals. The
cost is a parallel hierarchy that must be kept in sync, plus a boilerplate
adapter class.

Whether to keep or collapse this depends on how much the other refactors
change PlaybackDevice's public surface. If PlaybackDevice becomes simpler and
its public API stabilizes, the indirection may no longer be justified.

### Finding 7: Simulator concerns leak into PlaybackDevice

`_gen`, `_frame_index`, and `clear_queued_runtime_outputs()` exist primarily
for ESPSimulated's frame queueing. `ESPSimulated::debug_seek()` directly
manipulates protected base class members (`_t0`, `_state`, `_paused_t_rel`,
`_playback_uses_sync`, `_sync_offset`, `_engine`). This is a secondary
concern -- worth noting but not the first thing to address.

Given the current redesign constraint, `debug_seek` should not shape the first
pass architecture. The desired direction is to make simulator playback follow
the same rules as firmware playback, then feel the pain before adding a
simulator-only escape hatch back in.

### Finding 8: Background provisioning depends on the local-clock fallback

The controller can provision a stored animation on an ESP32 for local playback
when disconnected (`_cmd_provision_background` in `service.py:291`). The
firmware later loads and plays it locally (`try_start_background_` in
`firmware_app.cpp:230`) with `_device.handle_start(0)` -- no controller, no
sync. The `ESPDevice::playback_t0` fallback converts `t0=0` to `now_mono()`.

If the fallback is removed, this path must become timebase-aware:
- Provisioning must reject synced artifacts (controller-side gate)
- Firmware should verify `requires_sync == false` before starting it
- Or firmware passes `now_mono()` as t0 directly for local playback

### Finding 9: Mixed-timebase scenes need a validation rule

`_cmd_load_scene` (`service.py:221`) merges strips from multiple program
entries into a single `CompiledManifest`. Today all strips are treated
uniformly since there is no timebase concept. If timebase becomes a per-blob
property, a scene could combine synced and local strips. This should be
rejected at load time -- all strips in a scene should agree on timebase.

### Finding 10: Fire-and-forget transport constrains enforcement strategy

Start, jump, and resume are fire-and-forget on the wire
(`device_protocol.py:107`). The firmware does not ACK them
(`controller_connection.cpp:599`). The controller updates local state
optimistically on send (`network_device.py:196`). Only `handle_load` and a few
other commands use request/response with ACKs.

This means device-side rejection of "start without sync" would silently
desynchronize controller and device state. The sync readiness invariant must
be enforced controller-side, before the command is sent. Device-side checks
can exist as a safety net but cannot be the primary enforcement mechanism.

---

## Candidate Directions

### Direction A: Encode `requires_sync` in the blob

Add a `requires_sync` flag to the compiled artifact and blob header. On load,
PlaybackDevice (or the controller) knows whether the animation requires sync.
The controller gates start on `is_synced()` for synced programs. The
`ESPDevice::playback_t0` fallback and `_playback_uses_sync` flag disappear.

### Direction B: Inject clock access into PlaybackDevice and move sync behavior into SyncedClock

Instead of storing raw sync offset fields on PlaybackDevice
(`_sync_offset`, `_sync_valid`, `_playback_uses_sync`) and branching on them
throughout the playback logic, move that behavior under `SyncedClock`.
PlaybackDevice centralizes the choice in one helper driven by `_requires_sync`
and otherwise stays agnostic about sync math.

For synced programs:
- playback does not start until `is_synced()` is true
- `SyncedClock` publishes a remote-domain estimate; acceptance, smoothing,
  and large-correction policy are open (see `redesign_playback.md`)

For unsynced programs:
- `PlaybackDevice` reads `now_local_us()` from the same clock abstraction

This keeps the playback core small while still allowing sync policy to evolve.
It also fixes the ownership boundary: sync corrections are fed from
`ControllerConnection` into `SyncedClock`, not into `PlaybackDevice`.

### Direction C: Separate the rendering pipeline

Make the decode-Engine-Strip-tick pipeline directly usable without
PlaybackDevice. This could be as simple as making Engine construction from a
blob easier (it currently requires a separate `decode_program` call and manual
Strip setup). RenderDevice / `strip_render` becomes a standalone tool that uses
Engine directly.

---

## Decisions And Open Questions

1. Decision: the timing requirement should live on the program/artifact as
   `requires_sync`, not on the play command. This is now the working
   direction: it's knowable at creation time and this is early dev with no
   format-change cost.

2. If the rendering pipeline is extracted, does PlaybackDevice still need to
   own the Engine, or does it delegate to a separate Renderer?

3. What is the exact `SyncedClock` API? Current lean:
   - `is_synced()`
   - `now_remote_us()`
   - `now_local_us()`
   - if `now_remote_us()` is called while unsynced, returning `0` is an
     acceptable first pass as long as simulation/tests/assertions make misuse
     obvious
   PlaybackDevice itself should centralize the choice in one helper using
   `_requires_sync`.

4. Where does the sync estimator live?
   - controller computes candidate corrections, firmware `SyncedClock`
     accepts and publishes a remote-domain estimate
   - or firmware owns more of the smoothing/history logic directly
   Current code computes filtered offsets in controller `clock_sync.py`.
   Current lean: keep the heavier estimation/filtering work on the controller
   and let firmware `SyncedClock` stay small: `is_synced()`, remote-domain
   publishing, correction acceptance, and synced/unsynced transitions.

5. What is the small-correction policy inside `SyncedClock`?
   Options under discussion have included holding, jumping ahead, or slewing;
   the rendered-animation side of this is now covered by `Playback` owning
   monotonic accepted `t_program`, so the clock-level policy is open (see
   `redesign_playback.md`). Earlier bullets in this doc asserting that
   `SyncedClock` must never publish time that goes backward are superseded.

6. What should happen when a remote-synced show loses sync badly enough
   to leave the allowed band? This is a playback / firmware lifecycle policy,
   not just a clock-internal detail. Current desired direction: `SyncedClock`
   reports loss of sync; higher-level playback policy fades out to black and
   exits synced playback rather than continuing to drift. Silent fallback to
   unsynced playback is not part of the intended design.

7. `debug_seek` is intentionally out of scope for the first redesign pass.
   If it returns later, it should be added on top of the simplified playback
   model rather than shaping the core architecture up front.

8. Does ControllerDevice survive these changes, or does the simplified
   PlaybackDevice public API make it redundant?

9. Where should mixed-timebase scene rejection live? Likely in
   `_cmd_load_scene` at the service layer, before the combined manifest is
   built.

10. Should background provisioning validate timebase at the controller
   (reject synced blobs in `_cmd_provision_background`), at the firmware
   (reject in `try_start_background_`), or both?

11. The fire-and-forget transport means start admission is naturally a
   controller concern. If the design grows an explicit waiting/degraded state,
   should the transport/protocol reflect it, or is controller-side gating
   sufficient?

---

## Simplification Potential

If the directions above are adopted, the following code can likely shrink or
disappear:

- `PlaybackDevice` sync fields and helpers:
  - `_sync_offset`
  - `_sync_valid`
  - `_playback_uses_sync`
  - `handle_sync_result()`
  - `clear_sync()`
- `PlaybackDevice::tick_once()` / `current_t_rel()` no longer need the
  duplicated `effective_offset` logic
- `PlaybackDevice::playback_t0()` virtual becomes unnecessary
- `ESPDevice::playback_t0()` local fallback disappears
- controller-side policy can unify initial play, resume, seek, and rejoin
  around one explicit "synced program requires synced clock" rule
- background provisioning becomes self-validating once the artifact carries
  timebase information

This is the main redesign payoff: delete support for undesired runtime states
instead of preserving them behind flags and fallback branches.
