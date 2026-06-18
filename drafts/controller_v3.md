# Controller (v3)

What the Python controller must become. The device side is implemented
and verified (the full stack in `src/`, shared by the firmware and the
sim through the App); the Python under `controller/` is still the v2
controller, built on wire assumptions that v3 inverted. This doc
carries the controller's obligations on the v3 wire, the survey of the
existing code (what survives and what does not), the new pieces to
build, and a build order. The wire reference is in the appendices;
command behavior lives with the device handlers in
`src/command_handler.cpp`.

## The inversion

The v2 controller connected out to each configured device at a known
address,
identified it by a numeric `device_id`, measured clock offsets itself
and pushed corrections over TCP, and threaded a `gen` counter through
LOAD/JUMP to correlate preview frames. v3 removes all four: the device
connects out to the controller, the UID is the only identifier, sync is
device-initiated and device-computed, and no generation counter exists
anywhere on the wire.

That cuts the existing codebase in half along a clean line. Everything
that faces operator clients (the unix-socket service protocol, the
TUI, the web UI, the program library) survives with vocabulary
updates. Everything that faces devices is a rewrite, and the rewrite
is smaller than what it replaces: v3 deliberately puts the hard parts
(sync filtering, liveness math, reconnect policy) on the device, which
is already done and tested in C++. The Python side is left with three
small servers and a session table. Appendix D has the per-module
verdicts.

## How a device joins

**The device connects out; the controller only listens.** The
device broadcasts a UDP `DISCOVER` ("I'm `<uid>`"); a controller that
wants the device replies with a unicast `OFFER` ("I'm at
`<ip:tcp_port>`"). The device opens a TCP connection to that address
and writes one `REGISTER` message. The controller validates it and
either starts sending commands or silently closes.

Controller obligations:

- Answer every `DISCOVER` from a wanted device with a unicast
  `OFFER`, for as long as it wants the device. A device treats an
  OFFER older than two DISCOVER broadcast intervals (2 x 1.5 s) as a
  controller that has gone away and stops connecting to it, so a
  single OFFER is not enough; keep answering.
- There is no ACK for `REGISTER`: continued controller presence is
  acceptance, a silent close is rejection. Validating the UID and
  `protocol_version` is the controller's job.

The direction flip (vs. a device-side listener) is deliberate: the
device runs no listening socket, and client-to-known-server is the
conventional IoT shape. The cost, answering `DISCOVER` with `OFFER`,
sits on the capable side of the link.

## Liveness

The controller must send `Ping` at `PING_INTERVAL_MS`
(`src/link_protocol.h`) whenever it has no other commands to send. A
device drops the link after twice that interval without a handled
message and returns to discovery; TCP alone cannot detect a crashed
controller in useful time. Any handled command counts as liveness, so
a busy controller does not need to interleave pings.

## Every command ACKs

Every inbound command produces an `Ack` (0x80) carrying a status
byte; the controller should treat any non-Ok status as the command
not having happened (common case: a synced program refused with
`Unsynced` because the clock is not leased). Status values are in
`src/link_protocol.h`. A malformed message or a liveness timeout
drops the connection; an unknown opcode on a well-formed message ACKs
`UnknownCommand` and the link continues, the normal
protocol-evolution case.

## Reboot

After ACKing `Reboot` the device restarts: the controller sees the
TCP connection drop, then a fresh `REGISTER` with a new `boot_token`.
The changed `boot_token` is how the controller detects a fresh boot
and drops state cached for the previous one.

## Identity

The UID is the only device identifier; there is no numeric
`device_id` in v3. The slot format and the `esp-` / `sim-` prefix
conventions are in `src/device_identity.h`.

No cryptographic auth (HMAC, TLS). For a LAN-scoped controller with a
small fleet the threat model does not justify the cost: mbedTLS, key
provisioning, rotation, debugging. A remote (VPS) controller would
change that calculus; adding auth then is a v4 wire break, which this
project's no-legacy stance allows. `protocol_version` in `REGISTER`
is the escape hatch that keeps that v4 cheap: always sent, ignored
now.

Two identifiers live in the controller config and never reach the
wire:

- `strip_id`: the program-routing key. The DSL names each output by
  `strip_id` ("main", "left"), and the controller loads that output
  onto every device configured with the same `strip_id`: usually one,
  but several when an output is mirrored across devices.
- `label`: an optional UI display string; UIs fall back to the UID
  when it is absent.

The wire stays at one identifier and a device can be renamed for the
UI without touching the device or the protocol.

## Sim frame UDP (sim only)

Real firmware writes pixels to FastLED. The sim instead sends RGB
previews to the controller's frame port for UI rendering. The frame
header is `uid[16] + frame_index:u32 + t_program:f32 + rgb...`; the
UID lets the controller demux multiple sims, since loopback rules out
source-IP demux. This is its own protocol, not the controller link.

## The device hub

The device-facing half of the controller is three servers plus the
frame-preview receiver, all owned by one single-threaded tick loop
and all demuxing by UID, never by address:

- **OFFER responder** (UDP 6040). Listens for `DISCOVER`, answers
  wanted UIDs with unicast `OFFER`, continuously (see "How a device
  joins").
- **Link server** (TCP 6041). Accepts connections, reads `REGISTER`,
  validates UID and `protocol_version`, silently closes unwanted
  peers. Per session it queues outbound commands and correlates ACKs
  FIFO (the wire is strictly in-order), sends `Ping` when the queue
  has been idle for `PING_INTERVAL_MS`, and on a fresh `REGISTER`
  with a changed `boot_token` drops state cached for the previous
  boot.
- **Sync server** (UDP 6043). Stateless PONG responder, specified in
  `synced_clock.md` ("the controller's sync server"). The one
  cross-cutting requirement: it stamps t2/t3 from the same monotonic
  microsecond clock that timestamps `program_start_us` in
  Start/Resume, because that clock is the canonical session time.

The hub exposes per-UID device sessions to the layer above. The v2
shape, one `NetworkDevice` per configured device that connects out and
blocks the tick for up to a second per command ACK, does not survive
the direction flip and should not be recreated: command sends are
non-blocking, ACKs arrive through the same poll loop as everything
else.

## Sessions

The v2 session core (`controller.py`, `service.py`) got the design
right and the mechanics wrong for v3. What carries over as intent:

- One session at a time, identified by session id and epoch,
  surviving device detach.
- Transport attachment and active serving tracked separately (a key
  invariant); a device can be attached but not yet serving.
- Program routing keys off `strip_id`: a device serves the manifest
  strip whose `strip_id` matches its config, several devices sharing a
  `strip_id` mirror one strip, and a device whose `strip_id` is absent
  from the manifest detaches. A `strip_id` is unique within a manifest
  (the compiler enforces it), so there is no positional slot index and
  no per-device target override; the load identity is
  `(session_id, strip_id)`.
- Seek snaps to compiler-provided safe intervals; the controller
  leaves one frame period of headroom before an interval's end
  (`compiler.md`).
- Rejoin of a returning device: `LOAD`, `JUMP(safe_point)`, then
  `RESUME(program_start_us)` for a playing session; the same without
  the final `RESUME` for a paused one. If no safe interval remains in
  the current segment, the device cannot be safely rejoined yet.
- Multi-strip preview assembly: per-strip frames bucketed by frame
  index into whole program frames for observers.

What changes: sessions and frame demux key off UID; the ACK status is
the source of truth for whether a command happened (`Unsynced`,
`ProfileMismatch` and friends surface to the operator instead of
being assumed away by optimistic state mirroring); there is no `gen`
filter on preview frames (open item below); and the per-device
bookkeeping that v2 scattered across eight parallel dicts keyed by
`device_id` collapses into one session record per UID.

## Build order

Two tracks, converging at end-to-end playback:

**Track A, the device runtime path.**

1. Wire codecs (Appendix B) and the config schema update (Appendix
   D, `config.py`). Pure-bytes leaf modules, testable against the
   constants in `src/`.
2. The device hub, verified live against `./elemctl sim` through
   discover, register, ping, QueryDeviceStatus, reboot. The C++
   device is the reference implementation of the protocol, so wire
   misunderstandings surface here at their cheapest.
3. The session core on top of the hub.
4. The service and its unix-socket vocabulary, then the TUI and web
   updates.

**Track B, the v3 compiler** (`compiler.md`). Independent of track A
until `LOAD`: the device decoder rejects v2 blobs, so nothing plays
end to end until the v3 emitter lands, but everything before `LOAD`
works without it.

## Open items

- **`PING_INTERVAL_MS`** (`src/link_protocol.h`) is a provisional 5 s;
  revisit once the controller-side ping scheduler lands. The device
  timeout derives from it automatically.
- **Preview-frame correlation.** v2 used `gen` to discard frames from
  a previous load; the v3 preview packet carries only `frame_index`,
  which resets on load. Likely sufficient: clear assembly buckets on
  `LOAD`/`JUMP` and tolerate one stale packet. Not yet decided.
- **Sync visibility (resolved).** The controller no longer measures
  offsets, so the per-device clock columns read from the device's own
  `QueryDeviceStatus` report (a synced flag plus offset, RTT, and
  last-sync age) rather than a controller measurement; the controller
  polls it on a steady cadence. The field plumbing (firmware +
  `wire.py`) can land before the service rewrite wires the columns.
  See `synced_clock.md`.
- **`target_fps` / `requires_sync` transport** to the controller:
  `CompiledManifest` fields or blob-header parsing. The headroom rule
  needs `target_fps`. Tracked in `compiler.md`.
- **`elemctl run`.** Whether the one-shot compile-and-play CLI
  survives; in v3 a one-shot run has to stand up the whole hub, at
  which point it is the server.
- **Scene contract.** v2 `load_scene` requires all entries to share
  one duration and intersects their safe intervals. Keep, or wait for
  the scene/playlist design that `compiler.md` defers to.
- **Config shape.** Per-device `host` / `tcp_port` / `device_id` go
  away. Whether `device_type` stays or is inferred from the UID
  prefix, and the exact `label` semantics, are open.

---

## Appendix A: wire message format

Length-prefixed, both directions:

```
length: u32 (LE) | type: u8 | payload
```

`length` counts `type + payload`, not itself. Multi-byte values are
little-endian. All floats must be finite.

## Appendix B: opcode payloads

Opcode constants are in `src/link_protocol.h` and behavior lives with
the handlers in `src/command_handler.cpp`; the table below is the wire
payload only. Direction is `ctrl -> dev` unless noted.

| Opcode | Cmd                  | Payload                                              |
|--------|----------------------|------------------------------------------------------|
| 0x00   | `Register`           | `char uid[16], u32 boot_token, u8 protocol_version` (dev -> ctrl) |
| 0x01   | `SetProfile`         | `u16 strip_length`                                   |
| 0x10   | `Load`               | `u8[] blob`                                          |
| 0x11   | `Start`              | `i64 program_start_us`                               |
| 0x12   | `Jump`               | `f32 t_program`                                      |
| 0x13   | `Pause`              | (empty)                                              |
| 0x14   | `Resume`             | `i64 program_start_us`                               |
| 0x15   | `Stop`               | (empty)                                              |
| 0x16   | `PlayLocalAnimation` | `u16 order_index`                                    |
| 0x20   | `StoreAnimation`     | `char name[32], u8[] blob`                           |
| 0x21   | `EraseAnimation`     | `char name[32]`                                      |
| 0x22   | `SetAnimationOrder`  | `u16 count, char names[count][32]`                   |
| 0x30   | `Reboot`             | (empty)                                              |
| 0x40   | `QueryDeviceStatus`  | (empty); ACK payload below                           |
| 0x41   | `Ping`               | (empty)                                              |
| 0x42   | `QueryLocalAnimations` | (empty); ACK payload below                         |
| 0x80   | `Ack`                | `u8 status` + optional payload (both directions)     |

`uid[16]` and `name[32]` are null-padded ASCII (`UID_SIZE`,
`ANIM_NAME_SIZE`). `Register` is the first message device to controller
and is never inbound-dispatched. `0x02` is reserved (was `SyncLease`;
sync moved to UDP). Unknown high nibbles and stray inbound `0x8_`
replies ACK `UnknownCommand`.

`SetProfile` is a no-op when the length matches the active profile; on
change it persists the profile and requests a reboot so the new
geometry boots clean. `PlayLocalAnimation` loads and starts the stored
animation; looping on Ended is App policy keyed off
`AppContext::local_program_loaded`.

`QueryDeviceStatus` ACK payload: `u8 mode, u8 flags, u16 animation_count`.
Layout matches `DeviceStatus` in `src/device_status.h`.

`QueryLocalAnimations` ACK payload: `u16 count`, then `count` records
of `{ char name[32], u16 strip_length, u32 crc32 }`, in play order.
The crc32 is IEEE (Python `zlib.crc32`), computed over the whole
blob; the controller compares it against its own artifact to decide
whether a stored animation is stale.

## Appendix C: ports

Four contiguous ports (friendly for firewall rules):

| Port | Proto | Purpose                                          |
|------|-------|--------------------------------------------------|
| 6040 | UDP   | discovery: DISCOVER broadcast, OFFER unicast     |
| 6041 | TCP   | controller-link: controller listens, device connects |
| 6042 | UDP   | sim frame previews (sim only)                    |
| 6043 | UDP   | clock sync ping/pong                             |

The controller config holds all four. The device takes the TCP port
from each OFFER, hardcoding nothing about the controller; the
discovery and sync ports are compile-time well-known. The sim frame
port exists only in sim CLI flags and the controller UI receiver.

DISCOVER / OFFER packet layouts are in `src/discovery.{h,cpp}`.

## Appendix D: module survey

Verdicts for the existing code under `controller/` and the
controller-facing parts of `compiler/`. "Keep" means untouched or
near-untouched; "update" means the intent and structure stay and the
contents change; "rewrite" means the intent stays and the code does
not; "delete" means the reason to exist is gone.

| Module                   | Verdict | Why                                                        |
|--------------------------|---------|------------------------------------------------------------|
| `sim.py`                 | keep    | already rewritten for v3 (supervisor)                      |
| `slogger.py`             | keep    | generic logging setup                                      |
| `version.py`             | keep    | git-describe helper                                        |
| `sim_layout.py`          | keep    | CSV layout files, no wire contact                          |
| `library.py`             | keep    | program catalog + artifact cache, wire-independent         |
| `program_metadata.py`    | keep    | AST metadata extraction; revisit only if the DSL surface changes |
| `controller_protocol.py` | keep    | client protocol, independent of the device wire            |
| `controller_client.py`   | keep    | client of the above                                        |
| `server.py`              | keep    | unix-socket server shell; event vocabulary updates only    |
| `web.py`                 | update  | relay survives; full-state/device vocabulary changes       |
| `tui.py`                 | update  | dialogs survive; same vocabulary changes                   |
| `config.py` / `config_edit.py` | update | drop `device_id`/`host`/`tcp_port`, add the four ports and `label` |
| `render.py`              | update  | formatting half keeps; new compiler surface and the rewritten `strip_render` CLI |
| `controller.py`          | rewrite | session design keeps (see "Sessions"); `gen` and optimistic state go |
| `service.py`             | rewrite | command vocabulary and load planning keep; connectivity machinery assumed outbound connects |
| `device_protocol.py`     | rewrite | every opcode and payload is v2; the v3 codecs live in `wire.py`, this file goes with its v2 callers |
| `network_device.py`      | rewrite | becomes the link server; direction flip kills the rest     |
| `discovery.py`           | rewrite | HELLO/REJECT became DISCOVER/OFFER with roles swapped      |
| `udp_receiver.py`        | rewrite | trivial; new frame header, UID demux                       |
| `clock_sync.py`          | delete  | 448 lines of controller-side NTP filtering; v3 needs a stateless responder |
| `device.py`              | delete  | sim-vs-network abstraction; in v3 every device is on the real wire |
| `run.py`                 | delete? | one-shot runner; open item above                           |
| compiler `dsl.py` / `types.py` | update | builder keeps; `target_fps` / `requires_sync` declarations land here |
| compiler `compiler.py`   | rewrite | validation and time resolution keep; planning and analysis are v3 (`compiler.md`) |
| compiler `blob.py`       | updated | v3 byte format; source of truth is `docs/blob_format.md`   |

Notes that did not fit the table:

- **`service.py` state consolidation.** The per-device dicts
  (`_prev_connected`, `_last_seen`, `_last_activity_ns`,
  `_disconnect_reasons`, `_sync_ready_boot_token`,
  `_reported_status`, `_reported_at`, `_last_probe_ns`) all describe
  one device and drift independently. The rewrite folds them into one
  session record per UID, owned by the hub.
- **`device.py` duck typing.** Deleting the protocol also deletes the
  `getattr(dev, 'store_background', None)`-style probing in
  `service.py`; it existed for test fakes, which can implement the
  one concrete session class instead.
- **Replaced, not ported, commands.** v2's `ATTACH`,
  `STORE_BACKGROUND`, `CLEAR_BACKGROUND`, `SYNC_RESULT` and
  `DEBUG_SEEK` have no v3 counterparts. Background provisioning is
  superseded by the local-animation command family (0x16, 0x20 to
  0x22, 0x42), which the controller does not drive anywhere yet;
  provisioning must also refuse synced artifacts for offline use
  (the TODO matrix).
- **Known-stale facts.** `config.py` has `DEFAULT_FRAME_PORT = 9002`
  (spec: 6042). `controller/tests/sim_helpers.py` drives the deleted
  `network_sim` binary; integration tests rebuild on `sim_device`
  through the supervisor. `MAX_DEVICE_PIXELS = 300` is current.
- **Tests.** Suites pinned to v2 wire modules
  (`test_network_device`, `test_discovery*`, `test_clock_sync`,
  `test_network_sim_logging`, `test_orchestrated`,
  `test_multi_device`, `test_integration`) go with their modules.
  `test_sim`, `test_library`, `test_config*`, `test_slogger`,
  `test_sim_layout` survive; `test_tui` / `test_web` need only the
  vocabulary updates.
