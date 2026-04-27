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
