# TODO

Tracked deferred work. Each entry: enough context for an empty-context
Claude to act without spelunking.

---

## Write the v3 firmware entry point and app loop

**Today.** The v2 firmware files have been moved to `src/deprecated/`
(`controller_connection`, `discovery_service`, `firmware_app`,
`diagnostics`, `esp_device`, `main`). Nothing currently builds a
firmware binary. `src/firmware/` holds only the platform-impl files
that survive into v3 (`esp_tcp_transport`, `esp_udp_transport`,
`esp_file_store`, `esp_platform_clock`, `esp_device_identity`,
`nvs_key_value_store`, `wifi_manager`, `led0_sanity`).

**Action.** Build the v3 firmware owner:

- New `src/firmware/main.cpp`: construct the App with `EspNetworkInterface`
  (around `wifi_manager`), `DiscoveryClient`, `EspTcpTransport`,
  `EspUdpTransport`, `Playback`, `AnimationStore` over `EspFileStore`,
  `NvsKeyValueStore`, `DeviceStatus`, `EspSystemPlatform`, and the
  `AppContext` bundling them.
- App loop runs the three-sibling polling contract:
  `network.poll(); link.poll(); sync.set_controller(link.controller_ip_addr()); sync.poll();`
  then playback / render. After `link.poll()` returns, check
  `ctx.reboot_requested` and call `ctx.system.reboot()` once the ACK
  has flushed.
- `ControllerLink` owns the `CommandProcessor` internally and borrows the
  `CommandHandler` (the handler needs `AppContext`, which the App owns); the
  App only sees the link's `is_ready()` / `controller_ip_addr()` surface and
  the AppContext flags.

Depends on: `EspSystemPlatform` impl (not yet written; draft at
`drafts/system_platform.h`). `EspNetworkInterface` already exists in
`src/firmware/`; `HostNetworkInterface` is in `src/sim/`.

---

## Move JUMP `t_program` finiteness check to the wire layer

**Today.** `Playback::handle_jump(float t_program)` rejects non-finite
(`std::isfinite`) before the float→int cast in `src/playback.cpp`. This
prevents UB on `NaN`/`±Inf` regardless of caller, but it can only silently
return `Unchanged`.

**The better place is closer to the wire.** JUMP's `t_program` enters as 4
raw bytes that `WireReader::read_f32` produces at the handler:

- `CommandHandler::handle_jump_` (`drafts/command_handler.h`) — TCP
  path; can ACK `BadPayload` on `!std::isfinite(t_rel)` instead of
  silently rejecting at the Playback layer.

**Action.** Validate `std::isfinite(t_rel)` in the handler and ACK
`BadPayload`. `AckStatus` already has the needed code; no
`link_protocol.h` extension needed.

**Open question for that PR.** Whether to keep the Playback check as a
belt-and-suspenders invariant or drop it once the wire layer is honest.
The argument for keeping: future owners (test tools, fuzzers) bypass the
wire parser. The argument for dropping: Playback's other preconditions
trust their callers, and a lone finiteness check there is asymmetric.

---

## Wire LOAD path through `Playback::handle_load`

**Today.** The v3 `CommandHandler::handle_load_`
(`drafts/command_handler.h`) is unwritten; the v2 `handle_load_` in
`src/deprecated/controller_connection.cpp` used the v2 load signature
(`_device->handle_load(blob, blob_len, gen)` returning a plain bool)
with no `DecodeError` channel.

**Action.** When implementing `CommandHandler::handle_load_`, call
`Playback::handle_load(blob, blob_len, &err)` and map:

- `DecodeError::Ok`                  → `AckStatus::Ok`
- `DecodeError::StripLengthMismatch` → `AckStatus::ProfileMismatch`
- everything else                    → `AckStatus::Error`

Log `decode_error_name(err)` on every failed load so serial logs
identify the rejection reason without a debugger.

---

## Rewire `strip_render` off legacy `PlaybackDevice`

**Today.** `src/deprecated/strip_render.cpp` (offline CLI renderer)
routes through the legacy `PlaybackDevice` to render frames to stdout.
Post step 20 the v3 surface is `decode_program` + `Engine` + `Strip`
directly — no clock, no `Playback`.

**Action.** Rewrite the CLI to: call `decode_program(blob, blob_len,
strip_length, &err)`, reject `Program::requires_sync` (offline render
has no remote clock), `Engine::create(program)`, then drive
`Engine::render_frame(t_program, strip)` with explicit frame times
stepped at `1 / fps`. Drop the `RenderDevice : PlaybackDevice` shim.

---

## Playback

The three items below were the load-bearing content of the now-removed
`drafts/playback.md`. Each one is a post-step-20 wiring or policy
decision against `src/playback.{h,cpp}` that is not derivable from the
header or other drafts.

### Firmware and sim owner presentation loop

**Today.** `src/deprecated/esp_device.{h,cpp}` and
`src/deprecated/esp_simulated.{h,cpp}` still derive from the legacy
`PlaybackDevice`. There is no owner that drives `src/playback.{h,cpp}`;
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

### `handle_start` / `handle_resume` / `handle_jump` return status

**Today.** All three are `void` in `src/playback.{h,cpp}`. The v3
controller-link design requires them to return a status so
`CommandHandler` (`drafts/command_handler.h`) can ACK truthfully —
`Unsynced`, `WrongState`, `BadPayload`, `Ok`. Today the handler has to
infer the outcome from `state()` deltas, which can't distinguish a
rejection-by-`is_synced()` from a no-op call from the wrong state.

**Action.** Pick a status enum (likely a narrower playback-side type
that the handler maps onto `AckStatus` in
`drafts/command_handler.h`), update the three signatures, and rewire
`CommandHandler::handle_start_` / `handle_jump_` / `handle_resume_`
to map the returns onto the wire ACKs.

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

Sync lease acceptance, freshness, and large-correction policy stay on the
controller side — see `drafts/synced_clock.md`.
