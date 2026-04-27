# TODO

Tracked deferred work. Each entry: enough context for an empty-context
Claude to act without spelunking.

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
