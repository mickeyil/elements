# TODO

Tracked deferred work. Each entry: enough context for an empty-context
Claude to act without spelunking.

---

## Run the v3 firmware on hardware

**Today.** Both v3 entry points exist. Sim: verified live end to end
(discovery, register, reboot cycle through the supervisor). Firmware:
`src/firmware/main.cpp` + `EspFrameOutput` build and link
(`pio run -e esp32dev`, flash 66%, RAM 20%) but have not run on a
chip. `LED_PIN` is 13 and FastLED is registered `WS2812B/GRB`,
carried over from the led0 sanity sketch.

**Action.** Flash a real ESP32, watch serial for the boot line, and
walk the basic session against a controller: discover, register,
SET_PROFILE (reboots), LOAD/START renders to the strip. First-run
issues land here.

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

## Write the v3 `strip_render` offline CLI

**Today.** The v2 offline renderer (frames to stdout for golden tests
and debugging) was deleted with the rest of `src/deprecated/`; no
offline render path exists. The v3 surface for it is `decode_program`
+ `Engine` + `Strip` directly: no clock, no `Playback`.

**Action.** Write the CLI fresh: call `decode_program(blob, blob_len,
strip_length, &err)`, reject `Program::requires_sync` (offline render
has no remote clock), `Engine::create(program)`, then drive
`Engine::render_frame(t_program, strip)` with explicit frame times
stepped at `1 / fps`.

---

## Playback

The items below were the load-bearing content of the now-removed
`drafts/playback.md`. Each one is a post-step-20 wiring or policy
decision against `src/playback.{h,cpp}` that is not derivable from the
header or other drafts.

### Presentation loop leftovers

The owner loop itself is done: the App paces frames against
`target_fps()` and writes them through `FrameOutput`
(`EspFrameOutput` does gamma, channel order, zero-pad,
`FastLED.show()`; `SimFrameOutput` sends the preview packet). Two
pieces of the original design remain unimplemented:

1. **Slack telemetry.** Firmware should report
   `slack_us = frame_deadline_us - fastled_show_return_us` as
   composer-facing telemetry; cadence is not a LOAD admission gate.
   Blocked on the telemetry path (likely extra `QueryDeviceStatus` ACK
   fields); the deadline lives in the App's scheduler, so measuring
   needs a small seam between App and output.

2. **Black frame on failed LOAD.** v2 presented black when a LOAD
   failed mid-session so the strip never kept showing the dead
   program. v3 `Playback::handle_load` clears its buffer on failure
   but nothing presents it; the App should write black
   (`render_black_frame()`) when a LOAD fails while something was on
   the strip.

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
