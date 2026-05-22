# Controller Link

The device side of the controller wire: the outbound TCP dial, the
`REGISTER` handshake, and the `CommandParser` stack above the connected
socket. The class and its surface live in `drafts/controller_link.h`;
this doc carries the rationale and the wire reference the headers do
not. It replaces the v2 monolith (`src/firmware/controller_connection.{h,cpp}`)
and the duplicate parser in `src/deprecated/network_sim.cpp`.

## Where it sits

Three sibling modules cover everything between device and controller.
The App ticks them in dependency order each loop:
`network.poll(); link.poll(); sync.poll();`.

- `NetworkInterface`: is the LAN usable. Brings up Wi-Fi (sim: reports
  up). Reconnect is internal; nothing above gets a `reconnect()` knob.
- `ControllerLink`: is there an active command link. (this doc)
- `ClockSyncClient`: is `SyncedClock` fresh. A separate UDP loop; see
  `drafts/synced_clock.md`.

The link does not own Wi-Fi. Bundling them was considered, but
*network up, no controller, background playing* is a first-class app
state that gets muddier if Wi-Fi can vanish inside the link. Separate
siblings keep that state cheap to express.

## The polling contract

**The App polls modules unconditionally in dependency order; each
module self-gates and handles prerequisite loss.** No
`if (network.is_up())` guard wraps the link and sync calls. Each owns
state that must be reset on prerequisite loss (the link's socket and
parser buffer; the sync client's filter window and UDP socket).
Skipping `poll()` while the network is down would skip those resets
and resume from stale state.

The App bridges link to sync with one line,
`sync.set_controller(link.controller_ip_addr())`; the getter returns 0
when the link is not ready, so the setter idles on link-down and
re-targets on link-up with no conditional. Within one tick the cascade
settles: `network.poll()` drops `is_up()`, `link.poll()` tears down,
`set_controller(0)` passes the sentinel, `sync.poll()` idles.

## How a device joins

**The device dials out; the controller never connects in.** The key
direction-flip from v2.

`DiscoveryClient` broadcasts a UDP `DISCOVER` ("I'm `<uid>`"); a
controller that wants the device replies with a unicast `OFFER` ("I'm
at `<ip:tcp_port>`"). The link reads `discovery.controller_ip()` /
`tcp_port()`, opens an outbound TCP connection, and writes one
`REGISTER` frame. The controller validates it and either starts
sending commands or silently closes.

Outbound TCP earns the wire break twice over. The device runs no
listening socket: no attack surface, no duplicate-connection
rejection, no "is the server bound after Wi-Fi reconnect" dance.
And client-to-known-server is the conventional IoT shape, costing
nothing in operator intuition. The price, that the controller answers
`DISCOVER` with an `OFFER` rather than just listening, is small and on
the capable side of the link.

There is no separate ACK for `REGISTER`: continued controller presence
is acceptance, a silent close is rejection. This keeps the handshake
to one round-trip. Rejection surfaces as the next `read()` returning
`< 0`, or, if the controller goes silent without closing, as the
liveness deadline. In the brief window before either fires `is_ready()`
is true; a sync ping may reach a controller about to close, which is
harmless (unrecognized sync packets are discarded).

## Liveness

TCP alone will not tell the device a controller crashed in any useful
time. A hard-crashed or partitioned controller sends no FIN, no RST,
just silence; the kernel notices only on a write, and lwIP's keepalive
defaults are far too long. The v3 device mostly receives and writes
ACKs only in response, so a crashed controller would otherwise leave
it blocked in `read()` with `is_ready()` stuck true.

An app-level heartbeat closes the gap: the controller sends `Ping` on
an interval, the device tracks the last one, and a missed deadline
(`PING_TIMEOUT`) is treated as the controller gone. The timer field
and the deadline check are in `controller_link.h`; the `Ping` handler
is in `command_handler.h`.

Only `Ping` resets the timer, not arbitrary inbound traffic. A working
controller pings regardless of whether it is also sending commands, so
a controller sending commands but not pings is genuinely misbehaving
and the device declaring it dead is the correct signal.
`PING_TIMEOUT` is set `>= 2 x ping_interval` so a single dropped
packet does not drop the connection.

## Every command ACKs

v2's fire-and-forget on `Start` / `Pause` / etc. is gone. The
controller needs to know when a command was rejected (common case: a
synced program refused because the clock is not leased), or it lands
in a split-brain where it thinks the device is playing and the device
thinks the controller is confused. The `AckStatus` set is the enum in
`handler_result.h`.

A malformed frame (zero length, over `TCP_MSG_MAX`, socket EOF) and a
liveness timeout both drop the connection; an unknown opcode on a
well-formed frame just ACKs `UnknownCommand` and the link continues,
the normal protocol-evolution case. Every drop runs the same teardown:
close the socket, reset the parser buffer, reset the liveness timer,
return to discovery.

## Identity

The UID is the only device identifier; there is no numeric `device_id`
in v3. The slot format and the `esp-` / `sim-` prefix conventions are
in `src/device_identity.h`.

No cryptographic auth (HMAC, TLS). For a LAN-scoped controller with a
small fleet the threat model does not justify the cost: mbedTLS, key
provisioning, rotation, debugging. A remote (VPS) controller would
change that calculus; adding auth then is a v4 wire break, which this
project's no-legacy stance allows. `protocol_version` in `REGISTER` is
the escape hatch that keeps that v4 cheap: always sent, ignored now.

Two identifiers live in the controller config and never reach the
wire:

- `strip_id`: the program-routing key. The DSL refers to outputs by
  `strip_id` ("main", "left"), mapped one-to-one to a configured
  device.
- `label`: an optional UI display string; UIs fall back to the UID
  when it is absent.

Controller-side, the wire stays at one identifier and a device can be
renamed for the UI without touching the device or the protocol.

## Sim parity

`src/deprecated/network_sim.cpp`'s monolith collapses to socket setup,
the `Posix*Transport` classes from `src/sim/`, the same shared
parser / handler / `ClockSyncClient` the firmware uses, and the
sim-only frame loop below. The duplicate parser/dispatch/framing is
deleted in the same change; no half-migrated state. Sim runs the real
sync code against a controller on the same host; the measured offset
is ~0 because it genuinely is the same clock, not a stub.

## Sim frame UDP (sim only)

Real firmware writes pixels to FastLED. The sim instead sends RGB
previews to the controller's frame port for UI rendering. The frame
header is `uid[16] + frame_index:u32 + t_program:f32 + rgb...`; the
UID lets the controller demux multiple sims, since loopback rules out
source-IP demux. This is its own protocol, not the controller link.

## Open decisions

- **Reboot signal path out of `poll()`**: result struct vs. a getter
  read after each tick. Tracked as a TODO in `controller_link.h` and
  `command_handler.h`.
- **`ConnectionLayer` wrapper**: deferred until the App's
  `(network, link, sync)` orchestration proves duplicated between
  firmware and sim.
- **`PING_TIMEOUT` and ping interval values**: operational tuning;
  pin once the controller-side scheduler lands.

## Open items

- `Playback::handle_start` / `handle_resume` / `handle_jump` must
  return status. Tracked in `drafts/TODO.md` § Playback.
- `FileStore` needs a `PosixFileStore` sim impl (see
  `drafts/file_store.h`).

---

## Appendix A: wire framing

Length-prefixed, both directions:

```
length: u32 (LE) | type: u8 | payload
```

`length` counts `type + payload`, not itself. Multi-byte values are
little-endian. All floats must be finite.

## Appendix B: opcode payloads

Opcode behavior is described in `command_handler.h`; this table is the
wire payload only. Direction is `ctrl -> dev` unless noted.

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
`ANIM_NAME_SIZE`). `Register` is the first frame device to controller
and is never inbound-dispatched. `0x02` is reserved (was `SyncLease`;
sync moved to UDP). Unknown high nibbles and stray inbound `0x8_`
replies ACK `UnknownCommand`.

`QueryDeviceStatus` ACK payload: `u8 mode, u8 flags, u16 animation_count`.
`mode`: 0 attached_controlled, 1 detached_grace_hold, 2 detached_blank,
3 detached_background. `flags`: bit0 profile_present.

`QueryLocalAnimations` ACK payload: `u16 count`, then `count` records
of `{ char name[32], u16 strip_length, u32 crc32 }`, in play order.

## Appendix C: ports, and where the rest lives

Four contiguous ports (friendly for firewall rules):

| Port | Proto | Purpose                                          |
|------|-------|--------------------------------------------------|
| 6040 | UDP   | discovery: DISCOVER broadcast, OFFER unicast     |
| 6041 | TCP   | controller-link: controller listens, device dials |
| 6042 | UDP   | sim frame previews (sim only)                    |
| 6043 | UDP   | clock sync ping/pong                             |

The controller config holds all four. The device takes the TCP port
from each OFFER, hardcoding nothing about the controller; the
discovery and sync ports are compile-time well-known. The sim frame
port exists only in sim CLI flags and the controller UI receiver.

DISCOVER / OFFER packet layouts are in `src/discovery.{h,cpp}`. The
`AckStatus` codes are the enum in `drafts/handler_result.h`.
