# Controller Link

The TCP command wire between a device and the controller. The device
side is implemented (`src/controller_link.{h,cpp}` and the
`CommandProcessor` / `CommandHandler` stack above the socket); this
doc carries what the controller-side (Python) implementation needs
and cannot be read off that code: the controller's obligations, the
wire reference, and the cross-side rationale.

## How a device joins

**The device dials out; the controller never connects in.** The
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

- `strip_id`: the program-routing key. The DSL refers to outputs by
  `strip_id` ("main", "left"), mapped one-to-one to a configured
  device.
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

## Open items

- `PING_INTERVAL_MS` (`src/link_protocol.h`) is a provisional 5 s;
  revisit once the controller-side ping scheduler lands. The device
  timeout derives from it automatically.

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
| 6041 | TCP   | controller-link: controller listens, device dials |
| 6042 | UDP   | sim frame previews (sim only)                    |
| 6043 | UDP   | clock sync ping/pong                             |

The controller config holds all four. The device takes the TCP port
from each OFFER, hardcoding nothing about the controller; the
discovery and sync ports are compile-time well-known. The sim frame
port exists only in sim CLI flags and the controller UI receiver.

DISCOVER / OFFER packet layouts are in `src/discovery.{h,cpp}`.
