# TODO

Tracked deferred work. Each entry: enough context for an empty-context
Claude to act without spelunking.

---

## Rewrite `src/firmware/discovery_service.{h,cpp}` for v3

**Today.** The v2 discovery service binds the device-side UDP port
6040 and answers controller-initiated sync requests on the same
socket (`handle_sync_request_` in `src/firmware/discovery_service.cpp`).
The HELLO format is the v2 4-byte broadcast (`magic | tcp_port |
uid_len | uid[]`); the controller replies with a v2 OFFER that the
v2 controller-connection accept loop pairs with an inbound TCP
connection.

**Action.** Replace with the v3 discovery side described in
`drafts/controller_link.md` § "How a device joins" and Appendix C:

- HELLO is now `u16 magic | char uid[16] | u32 boot_token | u8 protocol_version`
  (23 bytes, broadcast every ~500 ms while no OFFER outstanding).
- OFFER carries `(controller_ipv4_be, tcp_port, nonce, ttl_ms)`. The
  device stashes a `ControllerOffer` snapshot and hands it to the
  controller link's TCP-dial path.
- Sync no longer rides this socket; sync UDP lives on its own port
  (6043) inside `src/clock_sync_client.cpp`. Drop the
  `handle_sync_request_` path entirely.
- Discovery is now an internal piece of `ControllerLink` per the
  draft, not its own top-level service. Decide during impl whether
  to fold the file into the link or keep it as a private collaborator.
- The new `DeviceIdentity` has `uid` as a NUL-terminated C string in
  `src/device_identity.h`; existing `strlen(_identity->uid)` calls
  still work but the wire format wants the fixed 16-byte slot
  (`min(strlen, UID_WIRE_SIZE)` bytes copied, rest zero-padded).

This work is currently broken on the v2 firmware build because the
v3 `DeviceIdentity` struct shape and `wire_constants.h` location
moved out from under it (intentional; firmware app is deprecated).

---

## Rewrite `src/firmware/controller_connection.{h,cpp}` for v3

**Today.** The 660-line v2 monolith binds a TCP server, accepts an
inbound controller connection, runs a handler switch on v2 opcodes
(`kCmdAttach = 0x06`, `kSyncResult = 0x03`, etc.), and tracks an
`attached` flag.

**Action.** Replace with the v3 device-initiated dial described in
`drafts/controller_link.md`:

- The device dials outbound TCP using the IP/port from the latest
  OFFER. No `WiFiServer` on the device.
- First message is `DEVICE_HELLO` (carries UID, `boot_token`,
  `offer_nonce`, `protocol_version`). See `drafts/device_hello.h`.
- Above the connected socket, run `CommandParser` from
  `drafts/command_parser.h` against the five handlers (Session,
  Playback, Storage, Status, System).
- Drop the v2 sync-result handler; sync is now a sibling on UDP.

Currently broken on the v2 firmware build for the same struct/header
movements as above.

---

## Rewrite `src/firmware/firmware_app.{h,cpp}` to wire v3 siblings

**Today.** `FirmwareApp::run_once` ticks `WifiManager`,
`DiscoveryService`, and `ControllerConnection` and centralizes the
attached/detached mode logic.

**Action.** Replace with the three-sibling polling contract:

- `network.poll()` (Wi-Fi up/down)
- `link.poll()` (discovery + dial + parser)
- `sync.set_controller(link.controller_ip_addr())` then `sync.poll()`

Mode logic keys off `link.is_ready()`. Handlers (Session, Playback,
Storage, Status, System) are constructed once at boot and reused
across reconnects; `CommandParser` is owned by the link.

Currently broken on the v2 firmware build for the same reasons as
the two items above.

---

## Move JUMP `t_program` finiteness check to the wire layer

**Today.** `Playback::handle_jump(float t_program)` rejects non-finite
(`std::isfinite`) before the float→int cast in `src/playback.cpp`. This
prevents UB on `NaN`/`±Inf` regardless of caller, but it can only silently
return `Unchanged`.

**The better place is closer to the wire.** JUMP's `t_program` enters as 4
raw bytes that `memcpy` into a float at the wire-protocol parser:

- `src/firmware/controller_connection.cpp::handle_jump_` — TCP path; can
  log the bad payload (seq, gen, hex) and ACK the controller with a
  meaningful error code instead of silently dropping the command.
- `src/sim_controller.cpp` — host-side caller that constructs `t_program`
  before calling `handle_jump`; same UB exposure if a NaN ever leaks in,
  but no wire to reject from.

**Action.** Validate `std::isfinite(t_rel)` in the wire parser(s), log
with full context, and ACK an error code (e.g., extend
`wire_constants.h` if the existing `kAckError` bucket is too coarse).

**Open question for that PR.** Whether to keep the Playback check as a
belt-and-suspenders invariant or drop it once the wire layer is honest.
The argument for keeping: future owners (test tools, fuzzers) bypass the
wire parser. The argument for dropping: Playback's other preconditions
trust their callers, and a lone finiteness check there is asymmetric.

---

## Wire firmware load path through `Playback::handle_load`

**Today.** `ControllerConnection::handle_load_` in
`src/firmware/controller_connection.cpp` still uses the v2 load
signature: `_device->handle_load(blob, blob_len, gen)` returning a
plain bool, no `DecodeError` channel. `wire_constants.h` only defines
`kAckOk`, `kAckError`, `kAckWrongState`.

**Action.**

1. Add `kAckProfileMismatch = 3` to `src/firmware/wire_constants.h`.
2. Rewire `handle_load_` onto `Playback::handle_load(blob, blob_len,
   &err)`. Mapping:
   - `DecodeError::Ok` → `kAckOk`
   - `DecodeError::StripLengthMismatch` → `kAckProfileMismatch`
   - everything else → `kAckError`
3. Log `decode_error_name(err)` on every failed load so serial logs
   identify the exact rejection reason without a debugger.

This is part of the post-step-20 firmware owner rewire called out in
`roadmap.md` Notes ("Old `src/` callers break during the migration").

---

## Rewire `strip_render` off legacy `PlaybackDevice`

**Today.** `src/strip_render.cpp` (offline CLI renderer) routes through
the legacy `PlaybackDevice` to render frames to stdout. Post step 20
the v3 surface is `decode_program` + `Engine` + `Strip` directly — no
clock, no `Playback`.

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

**Today.** `src/firmware/esp_device.{h,cpp}` and `src/esp_simulated.{h,cpp}`
still derive from the legacy `PlaybackDevice`. There is no owner that
drives `src/playback.{h,cpp}`; the post-step-20 owner rewire is unstarted.

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
`PlaybackHandler` (`drafts/playback_handler.h`) can ACK truthfully —
`Unsynced`, `WrongState`, `BadPayload`, `Ok`. Today the handler has to
infer the outcome from `state()` deltas, which can't distinguish a
rejection-by-`is_synced()` from a no-op call from the wrong state.

**Action.** Pick a status enum (likely shared with `AckStatus` or a
narrower playback-side type), update the three signatures, and rewire
`PlaybackHandler::handle_start_` / `handle_jump_` / `handle_resume_`
to map the returns onto the wire ACKs.

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
