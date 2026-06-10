# TODO

Tracked deferred work. Each entry: enough context for an empty-context
Claude to act without spelunking.

---

## Write the v3 entry points (firmware main, sim main)

**Today.** The shared App core exists (`src/app.{h,cpp}`, design in
`drafts/app.md`, tests in `test/test_app.cpp`): it owns the shared
objects, runs the tick order (network, link, reboot-after-ACK, clock
sync, mode placeholder, frame pacing), and presents frames through the
`FrameOutput` seam (`src/frame_output.h`). Nothing constructs it yet;
no firmware or sim binary builds.

**Action.** Per platform: implement the `FrameOutput` (`EspFrameOutput`
over gamma + channel order + FastLED; `SimFrameOutput` over the
frame-preview UDP), construct the concrete seam objects, hand them to
the App, and call `begin()` once and `tick()` forever. Firmware main
also needs the `platformio.ini` `build_src_filter` extended to the
shared sources the App pulls in (playback, command stack, link, sync,
wire, app). Detached-mode policy, loss-of-sync handling, and playlist
advance are open items inside the App; see `drafts/app.md`.

---

## Delete the deprecated v2 owners

**Today.** `src/deprecated/` and `test/deprecated/` (legacy
`ControllerDevice`, `SimDevice`, `ESPSimulated`, `network_sim`,
`strip_render`, and their tests) are staged for deletion. They are not
part of the normal build and exist only until the v3 firmware and sim
owners above replace them.

**Action.** Once the v3 entry points run, delete both directories and
their leftover CMake targets in the same change; no half-migrated
state.

---

## Log decode failures on LOAD

**Today.** `CommandHandler::handle_load_` (`src/command_handler.cpp`)
maps `DecodeError` onto the wire ACK (`StripLengthMismatch` →
`ProfileMismatch`, everything else → `Error`) but logs nothing;
`src/slogger.h` is host-only (`<mutex>`, `<fstream>`) and no shared
logging seam exists yet.

**Action.** Once the firmware logging story lands, log
`decode_error_name(err)` on every failed load so serial logs identify
the rejection reason without a debugger.

---

## Rewire `strip_render` off legacy `PlaybackDevice`

**Today.** `src/deprecated/strip_render.cpp` (offline CLI renderer)
routes through the legacy `PlaybackDevice` to render frames to stdout.
`PlaybackDevice` itself (`src/playback_device.{h,cpp}`) has been
deleted, so the old CLI no longer compiles; the rewrite below is the
only path.
Post step 20 the v3 surface is `decode_program` + `Engine` + `Strip`
directly — no clock, no `Playback`.

**Action.** Rewrite the CLI to: call `decode_program(blob, blob_len,
strip_length, &err)`, reject `Program::requires_sync` (offline render
has no remote clock), `Engine::create(program)`, then drive
`Engine::render_frame(t_program, strip)` with explicit frame times
stepped at `1 / fps`. Drop the `RenderDevice : PlaybackDevice` shim.

---

## Playback

The items below were the load-bearing content of the now-removed
`drafts/playback.md`. Each one is a post-step-20 wiring or policy
decision against `src/playback.{h,cpp}` that is not derivable from the
header or other drafts.

### Firmware and sim owner presentation loop

**Today.** `src/deprecated/esp_device.{h,cpp}` and
`src/deprecated/esp_simulated.{h,cpp}` still derive from the legacy
`PlaybackDevice` (now deleted from `src/`, so they no longer compile).
There is no owner that drives `src/playback.{h,cpp}`;
the post-step-20 owner rewire is unstarted.

**Action.** Build a thin firmware owner around `Playback`. On every
`Rendered` or `Ended` return from `render_next_frame()`:

1. apply the current gamma LUT to the strip
2. memcpy RGB into `g_leds` in the configured channel order
3. zero-pad trailing pixels when the strip is shorter than `MAX_STRIP_PIXELS`
4. call `FastLED.show()`
5. on `Ended`, run the end-of-program hook

A default-constructed `GammaCorrection` is identity; only `RGB` and `BGR`
channel orders are supported in the first pass. The owner times pacing
inside the budget defined by `Playback::target_fps()`; the budget covers
gamma + channel conversion + buffer copy + `FastLED.show()`, not just
engine math.

Cadence is **not** a LOAD admission gate. Firmware runs the program and
reports `slack_us = frame_deadline_us - fastled_show_return_us` as
composer-facing telemetry. Negative or near-zero slack is the signal that
the program is asking too much of the hardware at its declared
`target_fps`.

The sim owner runs the same shape, queueing frames for the sim harness
instead of calling `FastLED.show()`.

One v2 behavior did not carry over and needs a home in the owner: when
a LOAD failed mid-session, v2 `PlaybackDevice` presented a black frame
so the strip never kept showing the dead program. v3
`Playback::handle_load` clears its strip buffer on failure but returns
no presentation result, and `CommandHandler::handle_load_` only ACKs
the error, so the LEDs keep the last shown frame. The owner should
present black (`render_black_frame()`) when a LOAD fails while
something was on the strip.

---

### Online/offline × synced/unsynced matrix

**Today.** Nothing enforces the synced+offline rejection. Controller
service can provision a `requires_sync == true` blob for offline use;
firmware does not verify before starting stored offline content. Offline
render is covered separately by the `strip_render` rewire above, which
already rejects `Program::requires_sync` at the entry point.

|              | **Synced**      | **Unsynced** |
|--------------|-----------------|--------------|
| **Online**   | ✓               | ✓            |
| **Offline**  | ✗ (rejected)    | ✓            |

**Action.**

1. Controller-side provisioning rejects synced artifacts for offline use
   (controller `service.py` provisioning path).
2. Firmware verifies `requires_sync == false` before starting stored
   offline content.
3. Treat synced+offline as the invalid cell of the matrix, not a
   special-cased rule.

---

## Sim launcher: process-restart on reboot command

**Today.** No supervisor exists. The earlier design assumed in-process
simulated reboot (`SimSystemPlatform` resetting subsystems in memory);
that is being dropped in favor of process restart so the sim's RAM is
truly wiped, `boot_token` regenerates through the normal startup path,
and the "remember every piece of state to reset" hazard goes away. The
sim binary itself only needs to exit on reboot; the supervisor that
re-execs it is the deferred work tracked here.

**Action.**

1. Sim binary: on `CommandHandler` reboot request, flush the ACK then
   call `_exit(SIM_REBOOT_EXIT_CODE)` (e.g. 64). Normal exits (0) and
   crashes (other nonzero) stay as-is.
2. Supervisor lives in `elemctl sim` (or a small sibling). It execs
   the sim binary with the resolved argv and waits.
3. Reboot sentinel exit code: re-exec with the same argv. Any other
   exit code: surface it, do not restart.
4. Argv preserved across re-exec: `--device-uid`, log path, background
   storage path. `--tcp-port 0` (ephemeral) is fine; the relaunched
   process gets a new port and discovery announces it.
5. Crash-loop guard: more than N restarts within a short window means
   give up and surface the error.
6. Signal handling: `SIGTERM` is graceful exit, no restart. Abnormal
   death without the sentinel is also no restart.

**Depends on.** `CommandHandler` being available so the reboot opcode
can be wired to `_exit(SIM_REBOOT_EXIT_CODE)`. The in-process-reboot
detection in `ClockSyncClient` has already been removed; nothing on
the device side currently observes a runtime `boot_token` change.

---

## Sim UID policy enforcement in the launcher

**Today.** `make_sim_device_identity` in `src/sim/sim_device_identity.cpp`
copies whatever string it is handed into the wire slot, with only a
length sanity check. The wire spec
(`drafts/controller_link.md` § Identity) says sim UIDs must start with
`sim-` and be printable ASCII; that policy is currently unenforced
because the factory deliberately stays shape-only.

**Action.** When the Python launcher (`elemctl sim` / `controller/elemctl/sim.py`)
gets focus, validate `--device-uid` (or the config-resolved UID for the
selected device) before spawning the sim binary:

- non-empty, ≤ 16 bytes
- starts with `sim-`
- all bytes printable ASCII

Reject at the launcher with a clear error; never invoke the sim binary
with a UID that violates the policy. The device-side factory stays
trusting; controller-side `REGISTER` validation is the second line
of defence.

---

### Loss-of-sync lifecycle

**Today.** `SyncedClock::is_synced()` flips false automatically when the
lease expires (`src/synced_clock.h`). No higher-level firmware/playback
policy exists for what synced playback does on lease loss.

**Direction.**

- higher-level playback/firmware policy fades out to black on lease loss
- no silent fallback to unsynced playback for synced content
- recovery on lease return (e.g., controller-issued JUMP to the nearest
  safe interval) is not yet pinned down; lives with controller/firmware
  policy, not with `SyncedClock`

Sync measurement, filtering, and lease issuance are all device-side;
see `drafts/synced_clock.md`. This item is only about what playback
does when the lease lapses.
