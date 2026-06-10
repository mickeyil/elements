# Implementation Roadmap

Ordered plan for moving v3 draft modules into `src/` with tests. Each step
lands a module (header + impl where applicable) and its unit tests before
the next step starts. Design contracts live in `blob_format.md`,
`synced_clock.md`, `compiler.md` (Playback's contract is now the source
in `src/playback.{h,cpp}`); the time-naming vocabulary is in
`data_model.md`. This file is the migration sequence.

## Settled Policies

- **Colors cutover.** `gamma_correct` is gone; old callers intentionally
  break until step 12 rewires through the new gamma path. No shim layer.
  No legacy compatibility window.
- **SyncedClock test seam.** A small `now_us()` module
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
- **Animation port split.** `animations/paint` / `animations/wave` /
  `animations/spark` are mechanical three-view ports (no `src`, no
  `work`). `animations/shift` gets its own pass because `src` + `work`
  preservation is load-bearing.

## Open Decisions (resolve before the blocked step)

(none open)

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

Landed as `src/platform_clock.h` + `src/sim/host_platform_clock.cpp`
(host) + `src/firmware/esp_platform_clock.cpp` (ARDUINO). Single-function
seam: `int64_t now_us()`. The fake lives at
`test/test_platform_clock.{h,cpp}` and exposes `set_test_now_us` /
`advance_test_us`. Production impl is selected at link time. The host
TU is now compiled by `elements_core`; the ESP TU is added to
`platformio.ini`'s `build_src_filter` once firmware first consumes
`SyncedClock`.

### 4. `synced_clock.{h,cpp}` — DONE

Landed in `src/synced_clock.{h,cpp}`. Reads time through
`now_us()`; no `#ifdef ARDUINO`. Test target
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

### 9. `pixel_view.{h,cpp}` — DONE

Landed in `src/pixel_view.{h,cpp}` and added to `elements_core` for
build coverage. Hot-path methods (`operator[]`, `physical_index`,
`size`, `empty`, `is_storage_identity`, `has_physical_mapping`,
`is_physical_identity`) inline in the header; `initialize`, `reset`,
`clear`, dtor stay out of line. Coverage in `test_pixel_view`:
default-empty, null backing rejected, identity vs non-identity storage
(including caller-frees-the-input-array), identity vs non-identity
physical (including caller-frees-the-input-array), null physical array
with non-identity mapping rejected, all four combinations of (storage
× physical) including independence checks, `clear()` only zeroes
pixels reachable via the view, `reset()` returns to empty,
re-initialize replaces the binding. Passes under valgrind memcheck.

### 10. `pixel_views.{h,cpp}` — DONE

Landed in `src/pixel_views.{h,cpp}` and added to `elements_core` for
build coverage. Hot-path methods (`count`, `at`) inline in the header;
`initialize`, `reset`, dtor stay out of line. `PixelViewSpec` is the
public input record (each field maps 1:1 to a `PixelView::initialize`
argument; per-field comments removed in favor of cross-reference to
`pixel_view.h`). Coverage in `test_pixel_views`: default-empty,
zero-count init, null-specs failure, identity-storage construction,
write-through to the pool buffer, non-identity storage routing,
identity vs non-identity physical wiring, every canonicality
violation (storage_identity ↔ storage_indices, physical_identity ↔
has_physical_mapping, physical_identity ↔ physical_indices),
invalid `buffer_idx`, identity-storage size > buffer fails,
mid-table failure cleans up earlier views, reset returns to empty,
re-initialize replaces. Passes under valgrind memcheck.

### 11. `copy_ops.{h,cpp}` — DONE

Landed in `src/copy_ops.{h,cpp}` and added to `elements_core` for
build coverage. Hot-path methods (`count`, `at`) inline in the header;
`initialize`, `reset`, dtor stay out of line. `CopyOp` is the public
record (3 fields: `at`, `src_pixv_idx`, `dst_pixv_idx`); contents are
trusted (the decoder validates view indices, equal src/dst sizes,
sort order, and same-`at` uniqueness). Coverage in `test_copy_ops`:
default-empty, zero-count init, null-ops failure, basic record copy
and access, caller-frees-input semantics, input table order
preserved, same-`at` ops keep their input order, reset returns to
empty, re-initialize replaces. Passes under valgrind memcheck.

### 12. `strip.{h,cpp}` — DONE

Landed in `src/strip.{h,cpp}` and added to `elements_core` for build
coverage. Hot-path methods (`operator[]`, `size`, `empty`, `byte_size`,
`pixels`, `bytes`) inline in the header; `resize`, `reset`, `clear`,
`copy_to`, dtor, and free `apply_gamma` stay out of line. `copy_to` gained
a `dst_pixels` argument so a max-sized hardware buffer ends up with a
zeroed tail when the strip is shorter (the manual pattern in
`src/deprecated/esp_device.cpp`). Standalone test target `test_strip` links
`src/strip.cpp` + `src/gamma.cpp`. Coverage: default-empty,
`resize`/`reset`/re-resize zeroing, write-through via `operator[]`,
`bytes` aliases `pixels`, `clear` (including empty no-op), `apply_gamma`
identity vs `set_gamma(2.0f)` vs empty, `copy_to` RGB/BGR, truncation,
zero-pad, empty-strip zero-fill, `dst_pixels==0` no-op, null `dst` no-op.
`gamma_correct` is gone from new code; legacy `compositor.cpp` still
references it but is not part of the new build path.

### 13. `animation.h` + `layer.{h,cpp}` — DONE

Landed in `src/animation.h` and `src/layer.{h,cpp}` and added to
`elements_core` for build coverage. `Animation` is the PixelView-based
visual primitive (header-only, virtual `initialize`/`render`); the legacy
HSVA-buffer interface is gone. `Layer` adopts a decoder-allocated event
array, owns each event's `Animation*`, and exposes `count()`, `at(idx)`,
and stateless `active_at(t)` (half-open `[start, start+duration)`,
linear scan with sorted-order short-circuit). Standalone test target
`test_layer` uses a `FakeAnim` that bumps a live-instance counter so
ownership tests can prove every animation gets freed exactly once.
Coverage in `test_layer`: default-empty, `active_at` on empty,
`initialize` adopts (count + per-event field check), `reset` and dtor
free both array and animations, re-`initialize` replaces (and frees the
prior batch), `active_at` for before-first / on-start / mid-event /
on-end (exclusive) / gap / past-last / back-to-back schedule.

### 14. `compositor.{h,cpp}` — DONE

Landed in `src/compositor.{h,cpp}` and stays in `elements_core`.
`Compositor::composite(Strip&, PixelView* const* active_dst_views, count)`
clears the strip, then walks each non-null view in array order:
transparent pixels (`a <= 0`) skip, opaque (`a >= 1`) overwrite,
partial-alpha calls `rgb_alpha_blend(out[phys], fg, a)`. Physical
routing is `view->physical_index(i)`. The defensive
`has_physical_mapping()` guard from the draft was dropped (decoder
validates dst views; trust input matches CopyOps). Standalone
`test_compositor` target replaces the legacy v2 wiring; it links
`compositor.cpp` + `strip.cpp` + `pixel_view.cpp` + `colors.cpp`.
Coverage: zero-count clears the strip, nullptr-entry skipped, opaque
view writes `hsv_to_rgb` per pixel, transparent pixel leaves output
untouched, partial alpha blends with cleared base, opaque-top overwrites
bottom, semi-transparent top blends with bottom, mixed
active/inactive layers, non-identity physical mapping routes
view[i]->strip[phys[i]], short view leaves uncovered pixels black,
each composite re-clears.

### 15. `program.{h,cpp}` — DONE

Renamed from `program_structs.{h,cpp}` -- the file holds one type
(`Program`) plus `free_program()`, so the `_structs` suffix never fit.
Landed in `src/program.{h,cpp}` and added to `elements_core` for build
coverage. `Program` is a struct with deleted copy/move; `~Program()`
runs `delete[] layers` (each Layer dtor releases its events and
animations) and the embedded `PixelBufferPool` / `PixelViews` /
`CopyOps` tear down via their own dtors. `free_program(prog)` is the
thin `delete prog` wrapper; `nullptr` is safe. Standalone test
target `test_program` links `program.cpp` + `layer.cpp` +
`pixel_buffer_pool.cpp` + `pixel_view.cpp` + `pixel_views.cpp` +
`copy_ops.cpp`. Coverage: default-empty field state, default-dtor
no-op, populated-Program dtor frees layers/events/animations
(verified via FakeAnim live counter), `free_program(populated)`
matches `delete`, `free_program(nullptr)` no-op, embedded containers
initialize-and-tear-down cleanly, full Program (layers + pool + views
+ copy_ops) destructs cleanly. Drafts swept for cross-references:
`decoder.{h,cpp,md}` and `engine.h` now point at `program.h`. Engine
skeleton (`drafts/engine.cpp:69-70`) updated to use `layer.count()` /
`layer.at(...)` after step 13's encapsulation; the body otherwise
remains the step-16 sketch.

### 16. `engine.{h,cpp}` — DONE

Landed in `src/engine.{h,cpp}` and stays in `elements_core`. `Engine`
is the rendering driver: `create(Program*)` is the fallible factory
(takes Program ownership on success and on failure), `render_frame`
runs due copy ops, walks each layer to find the active event,
initializes newly-active events with `src`/`work`, renders into `dst`,
then composites into the Strip. `reset()` rewinds layer cursors,
re-arms initialize flags, rewinds the copy-op cursor, and zeroes every
pool buffer. Defensive `copy_view` size check dropped (decoder
validates equal sizes per CopyOps trust convention). Standalone
`test_engine` target replaces the legacy v2 fixture-driven test; links
the engine + compositor + strip + program + container sources, no
fixture dependency. `FakeAnim` records initialize/render call counts
plus last-call args; `SrcReadAnim` makes copy-op-before-render order
observable through the strip. Coverage: `create(nullptr)` benign,
empty Program clears strip, `render_frame` boundaries (`[0, duration)`
half-open), single-event activation lifecycle (initialize once, render
per frame), cursor advance to next event, src/work view passing,
multi-layer independent tracking, copy-op runs before render,
copy-op cursor advances only past due ops, `reset` re-arms initialize,
`reset` rewinds copy cursor (replay verified), `reset` zeroes pool
buffers, chained same-`at` copy ops execute in table order, top layer
wins on a shared physical LED (engine-to-compositor handoff).

### 17 + 18. `animations/{paint,wave,spark,shift}.{h,cpp}` — DONE

Bundled both steps into one landing. Each header defines its `*Params`
struct and class, depends only on `animation.h` + `blob_reader.h` (and
`colors.h` for paint, which exposes `hsva_t*` ownership). Each `.cpp`
holds the render math and a `from_blob(params, params_size, err_out)`
static factory: parse with a local `BlobReader`, validate per the
`blob_format.md` contract (every float finite, range checks,
mode/channel bounds, period/fade > 0), allocate via `new (std::nothrow)`. Errors
default to `InvalidField`; `OutOfMemory` is set explicitly on alloc
failure. `Paint` distinguishes solid (one color) from constant
mode (a blob-baked hsva array replayed into dst); render() trusts
`constant_count == dst.size()` per the contract. The decoder enforces
that equality post-`from_blob` via `Paint::constant_array_size()`
(sketched in `drafts/decoder.{cpp,md}` for step 19). v3
`ShiftParams` drops the legacy `buffer_id` -- the work view now comes
from the event's `work_pixv_idx`. `Shift::initialize(src, work)`
snapshots `src` into `work` and remembers `work` for `render`. After
the bundle landed the four animation files were moved into
`src/animations/` and renamed `Wave` / `Spark` / `Shift` / `Paint` so
the directory is the namespace and the C++ name doesn't repeat it. Test
target `test_animations` is now standalone (links the four anim
sources + blob_reader + pixel_view + colors). Coverage in
`test_animations` (30 cases, 144 assertions): wave channel selector,
midpoint at t=0, pixel_step phase shift, from_blob round-trip and
rejections (channel > 2, period <= 0, NaN, truncation); spark t=0
full alpha, quadratic ease-out, post-fade clamp, from_blob round-trip
and NaN/fade rejections; paint solid fill, per-pixel write, dst-size
capping (shorter and longer than count, sentinel preserved), from_blob
solid + per-pixel round-trips, mode/NaN rejections; shift initialize
snapshot, t=0 produces work contents, direction left/right offset,
circular wrap, non-circular fill, snapshot survives later src
mutation, from_blob round-trip and rejections (direction > 1, circular
> 1, NaN velocity).

### 19. `decoder.{h,cpp}` — DONE

Landed in `src/decoder.{h,cpp}`. `BLOB_VERSION` and `BLOB_MAGIC`
replace the draft `kBlobVersion` / `kBlobMagic` per the
SCREAMING_SNAKE policy. `animation_types.h` promoted alongside.
Single-pass `decode_program(blob, blob_len, profile_strip_length,
err_out)`: validates magic + version, parses + validates the header,
then the buffer-size table, pixel views (including index-array
bounds), copy ops (sorted-`at`, src/dst sizes match, same-`at`
duplicate-dst rejected), then layers and events. Per-event,
dispatches on `AnimType` to the right `Anim::from_blob()`, then runs
the post-checks for paint (constant size matches dst view) and shift
(src view required). Trailing bytes rejected. On any failure the
already-allocated `Program` is freed via `free_program()`. Standalone
`test_decoder` target replaces the legacy fixture-driven test; the
test/fixtures dependency is dropped (other legacy targets still
declare it). Coverage in `test_decoder` (37 cases, 49 assertions):
bad magic, bad version, truncated prefix, profile_strip_length == 0
(caller bug), reserved flag bits, target_fps == 0, strip_length 0 /
over-cap / mismatch, duration NaN / <= 0, layer_count over-cap,
buffer size over-cap, total pool bytes over MAX_POOL_BYTES, truncated
buffer-size table, pixel-view buffer_idx out of range, unknown flag
bits, storage-identity oversized, physical_identity without
has_physical, storage/physical indices out of range, copy-op
NaN-`at`, out-of-order `at`, src/dst size mismatch, src ==
PIXV_NONE, same-`at` duplicate dst, event start+duration over
program duration, dst view without physical mapping, unknown
anim_type, paint constant size mismatch, shift without src,
factory-rejection propagation, layer events overlap, trailing bytes,
plus minimal valid blob, requires_sync flag, full program with copy
ops + spark event.

### 20. `playback.{h,cpp}` — DONE

`_gen` / `_frame_index` (and the `gen` parameter on `handle_load` /
`handle_jump`) dropped from `Playback`; production firmware does not
consume them and the host-side sim/network paths track per-call gen
themselves. Resolves the only open decision blocking this step.

### 21. `udp_transport.h` + `sim/posix_udp_transport.{h,cpp}` + `firmware/esp_udp_transport.{h,cpp}` — DONE

ABC promoted from `drafts/udp_transport.h` to `src/udp_transport.h`
with rebind semantics phrased in terms of the current bound port and
empty datagrams folded onto `recv() == 0`. POSIX impl works on both
Linux and macOS off plain BSD sockets; uses `getsockname()` to record
the actual ephemeral port and exposes a concrete `local_port()` for
tests. ESP impl wraps `WiFiUDP` and is added to `platformio.ini`'s
`build_src_filter`. `test_udp_transport` covers lifecycle, rebind
rules, round-trip with source-address population, multi-datagram
drain, truncation, empty-datagram drop, and unbound-state behavior;
70 assertions across 11 cases, clean under valgrind.

### 22. `animation_store.{h,cpp}` — DONE

Promoted from `drafts/animation_store.h` to `src/animation_store.{h,cpp}`
and added to `elements_core` for build coverage. Two deviations from the
draft: `MAX_STORED_BLOB_BYTES` is dropped in favor of the wire cap
`MAX_BLOB_BYTES` (`src/link_protocol.h` already documents it as covering
stored blobs), and `playlist.txt` lines carry an optional cached crc32
(`<name> [<crc32 hex>]`) so boot never reads whole blobs on the normal
path. The store keeps an in-memory index built at construction: one
playlist read plus a 20-byte header peek per blob (`strip_length`,
`requires_sync`); a line without a parsable crc falls back to
recomputing it from the blob. Mutations write the blob first, then the
playlist atomically; a failed playlist write resyncs the index from
disk, and the crash window leaves either an ignored orphan `.anim` or a
stale crc the controller detects and re-uploads. Standalone
`test_animation_store` target uses an in-memory `FakeFileStore` with
fault injection and read-byte accounting. Coverage (16 cases, 124
assertions): entry metadata + on-disk files, append order, in-place
overwrite, name-rejection table (traversal, charset, length), malformed
blob table (short, magic, version, reserved flags, requires_sync,
oversize), full-store reject vs overwrite-allowed, erase compaction,
set_order permutation validation + persistence, read_blob round-trip,
boot rebuild from cached crcs (header-peek-only reads asserted), crc
recompute fallback, bad playlist lines + orphan/synced/corrupt files
skipped, failed-write resync, erase with failed file delete.

### 23. `app_context.h` + `command_handler.{h,cpp}` — DONE

Promoted from `drafts/` to `src/` and added to `elements_core`.
`AppContext` is as drafted plus `local_program_loaded`: set by
PlayLocalAnimation, cleared by a live LOAD; the App's render loop
restarts on Ended while it is set, which is what makes local
animations loop. `CommandHandler` drops the draft's `CommandReply`:
every handler is `AckStatus handle_x_(WireReader&)` (the two queries
also take the reply `WireWriter`), `handle()` returns the status, and
the processor reads the reply length off `WireWriter::bytes_written()`.
Every parse ends with `require_empty()`; trailing bytes ACK
BadPayload. Mappings: `PlaybackResult` 1:1 onto WrongState/Unsynced
with BadTime → BadPayload; `DecodeError::StripLengthMismatch` →
ProfileMismatch, other decode failures → Error. SetProfile preserves
the stored color order and gamma (the wire carries only strip length)
and requests reboot only on change. Standalone `test_command_handler`
target drives real `Playback` and `AnimationStore` over in-test fakes
(`FakeFileStore`, `FakeKeyValueStore`, `FakeSystemPlatform`) and the
fake platform clock. Coverage (20 cases, 93 assertions): unknown
opcodes (inbound REGISTER, reserved 0x02, stray ACK), ping + trailing
rejection, reboot flag without SystemPlatform call, load happy path /
empty / garbage / strip mismatch, start-pause-resume-stop lifecycle
with truthful WrongState ACKs, Unsynced start, jump NaN and
behind-cursor → BadPayload, SetProfile no-op vs persist+reboot vs
preserved order/gamma vs invalid lengths, store/erase/set_order
including permutation rejections, PlayLocalAnimation looping flag +
bad index + mismatch, both query reply layouts byte-for-byte.

### 24. `command_processor.{h,cpp}` — DONE

Promoted from `drafts/` to `src/` and added to `elements_core`. Two
draft revisions: `poll()` returns `PollResult { Idle, Handled, Fault }`
instead of bool, so the owner learns about unrecoverable stream state
(zero/oversized message length, read error) explicitly; the processor
never disconnects the transport itself and ControllerLink stays the
single owner of teardown. And the TCP unit is called a "message"
throughout ("frame" is reserved for rendered frames). Parsing as
drafted: fixed `TCP_MSG_MAX` RX buffer, one message per poll,
memmove-consume, ACK assembled into one buffer and sent with a single
all-or-fail write (best-effort; a failed write flips the transport
disconnected and the next poll faults). A static_assert pins
`REPLY_PAYLOAD_MAX` against the largest reply (QueryLocalAnimations at
full store capacity). Standalone `test_command_processor` target
drives the real handler stack over a `FakeTcpTransport` with chunked
reads and write-failure injection. Coverage (11 cases, 30 assertions):
idle, ping ACK byte-exact, chunked-arrival buffering, two messages
one-per-poll, zero/oversized length faults, read-error fault, unknown
opcode continues the stream, query reply inside the ACK message,
12 KiB garbage LOAD consumed and stream continues, reset_buffer drops
a partial message, failed ACK write then fault.

### 25. `controller_link.{h,cpp}` — DONE

Promoted from `drafts/` to `src/` and added to `elements_core`. The
draft's four-state machine collapsed to two: network-down is a guard at
the top of `poll()` and the connect plus REGISTER write complete inside
one tick (`connect()` is synchronous), so `LinkState` is just
`Discovering` / `Ready`. Failed connects are paced by
`CONNECT_RETRY_INTERVAL_MS` (1 s; `connect()` can block 500 ms per
attempt). Liveness as designed: `PollResult::Handled` bumps
`_last_activity_us`, silence past `PING_TIMEOUT_MS` drops the link; the
timeout is derived as `2 * PING_INTERVAL_MS`, with the interval pinned
in `src/link_protocol.h` as a protocol-level value both sides share. Every
failure (fault, read error, dead socket, network loss, REGISTER write
failure) runs the one `drop_link_()` teardown: disconnect, reset the
processor buffer, clear the controller ip, back to Discovering.
Standalone `test_controller_link` target runs the real DiscoveryClient
over a fake UDP transport (fed real OFFER packets) and the real
handler/processor stack over a fake TCP transport. Coverage (10 cases,
41 assertions): network-down no-op, no-OFFER idle, one-tick
connect+REGISTER byte-exact attach, connect retry pacing, REGISTER write
failure with later retry, ping-refreshed liveness then silence drop,
processor fault teardown with cached-OFFER reattach and clean fresh
stream, read-error teardown, network loss and return, dead socket
noticed between ticks.

## Notes

- **Old simulator callers are staged for deletion.** Legacy
  `ControllerDevice`, `SimDevice`, `ESPSimulated`, `network_sim`, and
  their tests live under `src/deprecated/` and `test/deprecated/` until
  v3 sim support replaces them. They are not part of the normal build.
- **Compiler-side work is parallelizable.** v3 compiler emits the new
  blob per `blob_format.md` regardless of runtime progress. Schedule
  compiler changes independently; do not gate runtime migration on
  them.
- **Offline render and firmware owners** are rewired after step 20
  onto the `Playback` / `SyncedClock` / `GammaCorrection` surface.
  See the `## Playback` section of `drafts/TODO.md` for the owner
  presentation contract, the online/offline matrix, and the
  loss-of-sync lifecycle.
