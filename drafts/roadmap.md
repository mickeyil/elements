# Implementation Roadmap

Ordered plan for moving v3 draft modules into `src/` with tests. Each step
lands a module (header + impl where applicable) and its unit tests before
the next step starts. Design contracts live in `data_model.md`,
`blob_format.md`, `decoder.md`, `playback.md`, `synced_clock.md`,
`compiler.md`; this file is the migration sequence.

## Settled Policies

- **Colors cutover.** `gamma_correct` is gone; old callers intentionally
  break until step 12 rewires through the new gamma path. No shim layer.
  No legacy compatibility window.
- **SyncedClock test seam.** A small `platform_clock::now_us()` module
  selects `esp_timer_get_time()` on ARDUINO and `steady_clock` on host;
  tests link a controllable implementation. SyncedClock itself stays
  concrete — no virtuals, templates, or callbacks. Pinned by
  `synced_clock.h:29-30`.
- **Constant naming cutover.** New or touched C++ constants use
  `SCREAMING_SNAKE_CASE`, not `kCamelCase`. Because modules land
  incrementally, update constants as their roadmap step is implemented
  rather than sweeping future draft-only modules early.
- **Engine tested with fakes first.** Concrete `anim_*.h` ports land
  after engine, not before. Engine tests use fake `Animation`
  subclasses to exercise event timing, copy-op ordering, layer
  activity, and reset/jump.
- **Animation port split.** `anim_paint` / `anim_wave` / `anim_spark`
  are mechanical three-view ports (no `src`, no `work`). `anim_shift`
  gets its own pass because `src` + `work` preservation is load-bearing.

## Open Decisions (resolve before the blocked step)

- **`Playback::_gen` / `_frame_index` ownership.** Header flags it as a
  `TODO`; `playback.md` says "may move to the owner." Decide before
  step 21. Not a blocker for earlier steps.

## Migration Process

Each module step starts by promoting the draft source into `src/` with
`git mv` where a draft file exists. Then fill in the implementation, add
or update tests, and wire only the new component into the build. Do not
update legacy/dead components just to keep them compiling unless the step
explicitly depends on them.

At the end of the step, update the relevant `drafts/*.md` docs and this
roadmap to say the module is implemented. Promoted draft source files should
not remain as duplicate source-of-truth files under `drafts/`.

## Order

Each step is: implement the module, write its unit tests, land it in
`src/`. Tests run both on host and (where relevant) on the ARDUINO
target.

### 1. `colors.h` / `colors.cpp` — DONE

Landed in `src/colors.{h,cpp}`. `hsva_t`, `rgb_t`, `hsv_to_rgb`, and
`rgb_alpha_blend` stay; `gamma_correct` is deleted; `hsva_t` layout is
asserted in `src/colors.h`.

### 2. `gamma.{h,cpp}` — DONE

Landed in `src/gamma.{h,cpp}` as `GammaCorrection` with
`IDENTITY_GAMMA`, `MAX_SUPPORTED_GAMMA`, and `DEFAULT_GAMMA`.
`apply_gamma(Strip&, const GammaCorrection&)` is deferred to step 12
with the v3 `Strip`.

### 3. `platform_clock.{h,cpp}` — DONE

Landed as `src/platform_clock.h` + `src/platform_clock_host.cpp`
(host) + `src/platform_clock_esp.cpp` (ARDUINO). Single-function seam:
`int64_t platform_clock::now_us()`. The fake lives at
`test/platform_clock_test.{h,cpp}` and exposes `set_test_now_us` /
`advance_test_us`. Production impl is selected at link time. The host
TU is now compiled by `elements_core`; the ESP TU is added to
`platformio.ini`'s `build_src_filter` once firmware first consumes
`SyncedClock`.

### 4. `synced_clock.{h,cpp}` — DONE

Landed in `src/synced_clock.{h,cpp}`. Reads time through
`platform_clock::now_us()`; no `#ifdef ARDUINO`. Test target
`test_synced_clock` is standalone (mirrors `test_gamma`) and links the
fake clock instead of any production impl. Coverage: initially
unsynced; sign-convention round-trip (positive and negative offsets);
strict-less-than lease boundary; `valid_for_us == 0` push-revoke;
re-apply refreshes the lease relative to current now; last-known
mapping persists across lease expiry; `clear_sync` wipes the offset
(post-clear `remote == local`); idempotent `clear_sync` after expiry;
re-apply after clear restores sync; year-scale `int64` arithmetic.

### 5. `hardware_profile.h` — DONE

Landed in `src/hardware_profile.h` as a header-only profile with
`MAX_STRIP_PIXELS`, `ColorOrder`, strip length, channel order, and output
gamma. Tests cover: default-constructed is invalid (`strip_length == 0`);
`[1, MAX_STRIP_PIXELS]` valid; `MAX_STRIP_PIXELS + 1` rejected; constructor
stores length + color order + gamma; equality covers all three fields.

### 6. `blob_limits.h` + `blob_reader.{h,cpp}` — DONE

Landed in `src/blob_limits.h` and `src/blob_reader.{h,cpp}`. Cap constants
use `SCREAMING_SNAKE_CASE` and reuse `MAX_STRIP_PIXELS` from
`hardware_profile.h`. Tests cover cap values, `decode_error_name()`,
little-endian scalar reads, boundary failure without cursor movement,
`read_bytes()`, `take()`, null buffers, `done()`, and `remaining()`.

### 7. `runtime_constants.h` — DONE

Landed in `src/runtime_constants.h`. Header-only with `PIXV_NONE` and
`PIXBUF_NONE` (both `0xFFFF`). No tests — the header is two constants
with self-evident values; a unit test would just restate them.

### 8. `pixel_buffer_pool.{h,cpp}` — DONE

Landed in `src/pixel_buffer_pool.{h,cpp}` and added to `elements_core`
for build coverage. Two test targets share one source:
`test_pixel_buffer_pool` exercises the host (per-buffer-allocation)
path, and `test_pixel_buffer_pool_pooled` exercises the pooled
(`-DARDUINO`, contiguous) path. The pooled target also runs an
`#ifdef ARDUINO` adjacency block. Coverage: default-empty,
zero-buffer init, null-with-non-zero-count fails, allocation with
sizes preserved, zero-size entry allowed, out-of-range buffer_at /
buffer_size, per-buffer write isolation, reset returns to empty,
re-initialize replaces, pooled adjacency. Both targets pass under
valgrind memcheck.

### 9. `pixel_view.{h,cpp}`

Tests cover: identity storage, non-identity storage, identity
physical, non-identity physical, all four combinations, and that
`view[i]` and `view.physical_index(i)` are independent.

### 10. `pixel_views.{h,cpp}`

Built once from `PixelViewSpec[]`. Test spec → view materialization
and index bounds.

### 11. `copy_ops.{h,cpp}`

Small. Test basic construction, sort-by-`at` invariant (consumed from
the blob as already sorted), and access by index.

### 12. `strip.{h,cpp}`

Canonical linear-RGB buffer. Tests: construction at a given length,
clear, `apply_gamma` with `GammaCorrection`, `copy_to` with `RGB` and
`BGR` ordering, zero-pad semantics when destination is longer than
strip. This is the step that removes the last `gamma_correct` caller.

### 13. `animation.h` + `layer.{h,cpp}`

Structural. `Layer::initialize` + `Layer::active_at(t)` tests against
a trivial event list (use a fake animation type).

### 14. `compositor.{h,cpp}`

Depends on `PixelView` and `Strip`. Tests: single active layer,
multiple layers bottom-to-top, inactive layers skipped, physical
mapping applied per view.

### 15. `program_structs.{h,cpp}`

Program ownership + `free_program()`. Tests: destructor releases all
owned tables; post-`free` state is safe to destroy again.

### 16. `engine.{h,cpp}`

Render order per `data_model.md §Engine`. Tests use **fake
Animation** subclasses to exercise: event initialize on activation,
render each frame, copy-ops fired before rendering, `active_dst_views`
tracking, `reset()` clears initialized flags, jump semantics via
`run_copy_ops_until`.

### 17. `anim_paint.{h,cpp}` + `anim_wave.{h,cpp}` + `anim_spark.{h,cpp}`

Mechanical v3 ports: `render(dst, t_animation)` only, no `src`, no
`work`. Port the existing render math to the three-view interface;
add each type's `from_blob()` factory per `decoder.md §Animation
Construction`.

### 18. `anim_shift.{h,cpp}`

Careful pass: `initialize(src, work)` snapshots source pixels into
`work`; each `render(dst, t_animation)` reads `work` and writes `dst`
at the shifted offset. Tests must cover: fresh initialization snapshot
integrity, work view not mutated by later sources, shift offset sign
and wrap at `t_animation = 0` / mid / end.

### 19. `decoder.{h,cpp}`

Single-pass decode, blob → `Program`. Pulls in every `anim_*.h`
`from_blob()`. Tests: valid blob decodes; each per-section error path
surfaces the right `DecodeError`; over-cap / truncation cases; copy-op
ordering accepted / rejected per `blob_format.md`; animation-type
dispatch reaches the right factory.

### 20. `playback.{h,cpp}`

**First resolve `_gen` / `_frame_index` ownership.** Then implement the
state machine per `playback.md §Command Lifecycle`. Tests: each
command from each admissible state; synced program rejection while
`SyncedClock::is_synced() == false`; JUMP monotonicity via
`_t_program_cursor_us`; RESUME not gated; `handle_stop` clears strip;
natural-end transitions `PLAYING → ENDED` exactly once.

## Notes

- **Old `src/` callers break during the migration.** Legacy
  `PlaybackDevice`, `ControllerDevice`, `SimDevice`, `ESPSimulated`,
  and their tests depend on v2 shapes. They get retired (or rewritten
  as thin owners) after step 20, not kept alive on the side. Tests
  that block the build during the window are tolerated.
- **Compiler-side work is parallelizable.** v3 compiler emits the new
  blob per `blob_format.md` regardless of runtime progress. Schedule
  compiler changes independently; do not gate runtime migration on
  them.
- **Offline render and firmware owners** are rewired after step 20
  onto the `Playback` / `SyncedClock` / `GammaCorrection` surface
  described in `playback.md §Firmware / Sim / Offline-render`.
