# TODO

Tracked deferred work. Each entry: enough context for an empty-context
Claude to act without spelunking.

---

## Write the v3 firmware entry point

**Today.** The shared App core exists (`src/app.{h,cpp}`, design in
`drafts/app.md`, tests in `test/test_app.cpp`), and the sim side is
complete: `SimFrameOutput`, `src/sim/main.cpp` (flags: `--device-uid`,
`--controller-host` for unicast discovery, `--frame-port`), CMake
target `sim_device`. No firmware binary builds yet.

**Action.** Implement `EspFrameOutput` (gamma LUT, channel order,
zero-pad to `MAX_STRIP_PIXELS`, `FastLED.show()`, slack telemetry) and
`src/firmware/main.cpp` (construct the Esp seam objects, `app.begin()`
in `setup()`, `app.tick()` in `loop()`). Extend the `platformio.ini`
`build_src_filter` to the shared sources the App pulls in (playback,
command stack, link, sync, wire, blob_reader, app). Detached-mode
policy, loss-of-sync handling, and playlist advance are open items
inside the App; see `drafts/app.md`.

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
