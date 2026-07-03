# Protocol

How the controller and devices talk to each other, and how the web app
talks to the controller.

A device always connects *out* to the controller; the controller only
listens. The two then use four network channels, on separate ports.
Another channel, a local unix socket, connects the controller to the
web app.

## Ports

Five ports, kept next to each other so they are easy to allow through a
firewall. The controller config holds all five, but note that devices
know 6040, 6043 and 6044 as compile-time constants (the link and frame
ports travel in the OFFER and argv): changing those three in the config
alone breaks devices built with the defaults.

| Port | Type | Used for |
|------|------|----------|
| 6040 | UDP  | discovery (find the controller) |
| 6041 | TCP  | the controller link (commands and replies) |
| 6042 | UDP  | sim frame previews (simulator only) |
| 6043 | UDP  | clock sync |
| 6044 | UDP  | device logs |

## Discovery (UDP 6040)

The device finds the controller on its own. It broadcasts a `DISCOVER`
packet ("I am `<uid>`") every 1.5 seconds. A controller that wants that
device replies with an `OFFER` sent straight back ("connect to me at
`<ip:port>`"). The device then opens a TCP link to that address.

The device keeps broadcasting even after it connects. If two broadcast
intervals pass with no fresh `OFFER`, it assumes the controller is gone
and stops trying to reach it, so the controller must keep answering for
as long as it wants the device.

Both packets start with a 2-byte magic value, `0xD1CC`, that filters out
unrelated traffic on the port. Multi-byte fields are little-endian; the
IPv4 address is in network order.

```
DISCOVER  device -> broadcast   magic(2) type=0x01(1) uid(16)        = 19 bytes
OFFER     ctrl   -> device       magic(2) type=0x02(1) ipv4(4) port(2) = 9 bytes
```

The packet layouts live in `src/discovery.{h,cpp}`.

## Controller link (TCP 6041)

The reliable channel for commands and their replies. Every message,
both directions, has the same frame:

```
length: u32 (LE) | type: u8 | payload
```

`length` counts the type byte and the payload, but not itself.

The first message is always `REGISTER`, sent by the device right after
it connects. It carries the device's identity; there is no reply. If the
controller is happy with it, it starts sending commands; if not, it just
closes the connection.

```
REGISTER  device -> ctrl   uid(16) boot_token(u32) protocol_version(u8)
```

`uid` is a 16-byte null-padded ASCII slot (`UID_SIZE`). `boot_token`
changes on every reboot, so a fresh token tells the controller the
device restarted and any state cached for the old boot is stale.

### Commands

Every command's type and payload. Direction is controller to device
unless noted. Opcodes are defined in `src/link_protocol.h`; what each
command does lives with its handler in `src/command_handler.cpp`.

| Type | Name | Payload |
|------|------|---------|
| `0x00` | REGISTER | (device to controller; see above) |
| `0x01` | SET_PROFILE | `strip_length: u16` |
| `0x10` | LOAD | `blob: bytes` |
| `0x11` | START | `program_start_us: i64` |
| `0x12` | JUMP | `t_program: f32` |
| `0x13` | PAUSE | _(none)_ |
| `0x14` | RESUME | `program_start_us: i64` |
| `0x15` | STOP | _(none)_ |
| `0x16` | PLAY_LOCAL_ANIMATION | `order_index: u16` |
| `0x20` | STORE_ANIMATION | `name(32), blob: bytes` |
| `0x21` | ERASE_ANIMATION | `name(32)` |
| `0x22` | SET_ANIMATION_ORDER | `count: u16, name(32) x count` |
| `0x30` | REBOOT | _(none)_ |
| `0x40` | QUERY_DEVICE_STATUS | _(none)_; status in the reply |
| `0x41` | PING | _(none)_ |
| `0x42` | QUERY_LOCAL_ANIMATIONS | _(none)_; list in the reply |
| `0x80` | ACK | `status: u8` + optional payload (both directions) |

`name` is a 32-byte null-padded ASCII slot (`ANIM_NAME_SIZE`).
`SET_PROFILE` does nothing if the length already matches; on a change it
saves the new length and reboots so the device starts clean.
`PLAY_LOCAL_ANIMATION` loads and starts a stored animation by its place
in the play order.

### Replies

Every command gets an `ACK` back, carrying one status byte. The
controller should treat any status other than OK as "the command did not
happen".

| Status | Value | Meaning |
|--------|-------|---------|
| OK | 0 | done |
| Error | 1 | failed |
| WrongState | 2 | not allowed right now (e.g. LOAD before SET_PROFILE) |
| ProfileMismatch | 3 | the blob's strip length is not the device's |
| BadPayload | 4 | the message did not parse, or a value was out of range |
| UnknownCommand | 5 | the type byte is not a known command |
| Unsynced | 6 | a synced program was started without a live clock lease |

An unknown command type still gets an ACK (`UnknownCommand`) and the
link stays up; this is how the protocol can grow. A message that does
not parse at all, or a long silence (see below), drops the connection.

Two queries return extra bytes after the status:

`QUERY_DEVICE_STATUS` returns the device's current state:

```
mode: u8                (attached / detached / ...)
flags: u8               (bit0 = a strip profile is set)
animation_count: u16    (stored local animations)
```

`QUERY_LOCAL_ANIMATIONS` returns the stored play order:

```
count: u16
then count records of:  name(32), strip_length: u16, crc32: u32
```

The `crc32` is the standard zlib CRC over the whole blob; the controller
compares it against its own copy to tell whether a stored animation is
out of date.

### Keeping the link alive

When the controller has nothing to send, it sends a `PING` every
`PING_INTERVAL_MS` (5 seconds, in `src/link_protocol.h`). Any handled
command counts the same as a ping, so a busy controller does not need to
interleave them. If the device hears nothing for twice that interval, it
drops the link and goes back to discovery; TCP on its own cannot notice
a crashed controller in useful time.

## Clock sync (UDP 6043)

The device runs the sync, not the controller. It sends a `PING`, the
controller stamps it and replies `PONG`, and the device does the usual
four-timestamp math to work out its offset from the controller's clock.
The controller's only job is to answer; it keeps no per-device state and
never computes an offset itself.

```
device -> ctrl   PING   type=0x01 uid(16) device_boot_token(u32) seq(u32) t1(i64)   = 33 bytes
ctrl -> device   PONG   type=0x02 controller_boot_token(u32) seq(u32) t1(i64) t2(i64) t3(i64) = 33 bytes
```

`t1` is the device's send time, `t2`/`t3` the controller's receive and
reply times, and `t4` (the device's receive time) is never sent. The
controller must stamp `t2` and `t3` from the same clock it uses for
`program_start_us` in START and RESUME, because that clock is the one
the show runs on.

The device design (filtering, how long an offset is trusted, the ping
schedule) is in `synced_clock.md`; the device code is
`src/clock_sync_client.{h,cpp}` and `src/synced_clock.h`.

## Sim frame previews (UDP 6042, simulator only)

Real firmware writes pixels to the LED strip. A simulator has no strip,
so it sends each rendered frame to the controller instead, for the web
app to draw. This channel does not exist on real hardware.

```
uid(16) | frame_index: u32 | t_program: f32 | rgb bytes
```

The `uid` lets the controller tell simulators apart, since they all send
from `127.0.0.1`. `rgb` is 3 bytes per pixel.

## Device logs (UDP 6044)

Devices forward their log lines to the controller, one datagram per
record, fire and forget. Each line is also echoed locally (the ESP's
serial port, the sim's terminal), so the channel adds visibility, it
does not replace anything. The controller writes accepted records into
its own log, uid-prefixed; only configured UIDs are accepted, but a
device does not need a live link, so registration failures are exactly
what this channel can show.

```
device -> ctrl   magic(2)=0xD16C version(1)=1 uid(16) boot_token(u32)
                 seq(u32) uptime_ms(u32) level(u8) text(rest)
```

`seq` counts from 1 per boot; `boot_token` tells reboots apart from
reordering. The device buffers records in a small ring (16 records)
and ships only while the controller is evidently alive (link up, or a
fresh OFFER), so a short controller outage is bridged by the ring.
Anything beyond that is lost by design: the controller logs "lost N
records" when it sees a sequence gap, covering ring overflow and
dropped packets alike. `level` is ASCII `I`/`W`/`E`. `uptime_ms` is the
device's own clock (sync may be down when it matters); the controller's
log stamps arrival time.

The device side is `src/slog.{h,cpp}` (ring and local echo) and
`src/log_sender.{h,cpp}` (wire format and sending); the controller
side is `LogReceiver` in `controller/elemctl/hub.py`.

## Controller API (unix socket)

The web app talks to the controller over a local unix socket
(`/tmp/elemctl.sock`). A client connects as a **writer** (may send
commands) or an **observer** (receives state and frames only).

Every message uses the same frame:

```
length: u32 (LE) | kind: u8 | payload
```

There are two kinds: `0x01` is UTF-8 JSON (commands, replies, events,
state), `0x02` is a binary program frame.

The first message a client sends is `hello`, naming its role:

```json
{"id": 0, "cmd": "hello", "role": "writer", "protocol_version": 2}
```

The controller replies, then sends a full state snapshot.

### Commands (writer to controller)

```
load, play, pause, resume, stop,
add_device, edit_device, remove_device,
publish, rescan, list_programs, get_state, shutdown
```

`subscribe_frames` and `unsubscribe_frames` are allowed from any role,
since watching the preview is separate from controlling playback. Each
command carries an `id` the controller echoes in its reply, which it
sends only after the command's work is finished.

### State and events (controller to all clients)

The controller pushes the **whole** current state whenever it changes,
rather than small incremental updates: the loaded session (with its
strip list) and every device (uid, status, strip, label, and what the
session wants of it). On top of that it sends a few one-off events:

```json
{"type": "event", "event": "member_attached", "uid": "sim-1", ...}
{"type": "event", "event": "member_detached", "uid": "sim-1", ...}
{"type": "event", "event": "member_command_failed", ...}
```

### Program frames (kind = 0x02)

A program frame is the preview for one moment of the show, with every
strip's pixels joined together:

```
frame_index: u32 | t_program: f32 | rgb for strip 0 + strip 1 + ...
```

The strip order and lengths come from the loaded session's strip list.
The controller builds one program frame by collecting each strip's
preview for the same moment; it groups them by `floor(t_program * fps)`
so two synced devices land in the same frame. It only sends a frame once
every strip is present, and only while at least one client is
subscribed.

## Time

Two ways of expressing time:

- **Absolute** (`i64`, microseconds) on the wire between controller and
  device. START carries `program_start_us`; the device works out how far
  into the show it is by comparing that to its synced clock.
- **Program time** (`f32`, seconds from the start of the show) inside
  the engine and in preview frames.
