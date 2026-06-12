# Clock Sync

How a device keeps `SyncedClock` fresh against the controller's clock.
The device side is implemented (`src/synced_clock.h` is the passive
offset-and-lease container, `src/clock_sync_client.{h,cpp}` is the
feeder that measures the offset); this doc explains how the pieces
work together and what the controller-side (Python) sync server must
implement.

## How it works

Sync is device-initiated, over its own UDP port (6043). The device
sends a `PING`, the controller stamps it and replies `PONG`, and the
device runs the standard NTP four-timestamp formula on
`(t1, t2, t3, t4)`:

```
device → controller   PING   uid + device_boot_token + seq + t1
controller → device   PONG   controller_boot_token + seq + t1 + t2 + t3
```

`t1` is the device's send time, `t2`/`t3` the controller's receive and
reply times, `t4` the device's receive time. Each round yields an
`(offset, RTT)` pair that feeds a small filter; when the filter is
confident it calls `SyncedClock::apply_sync_offset` with a self-issued
lease (55 s). Playback consults only `SyncedClock::is_synced()`; a
synced program with no active lease is refused with `Unsynced`.

The App wires sync to the controller link with one line per tick:

```
network.poll();
link.poll();
sync.set_controller(link.controller_ip_addr());  // 0 when !is_ready
sync.poll();
```

`ClockSyncClient` knows nothing about the link beyond that IP; clock
estimation and link authority stay separate, meeting only in this
line.

## Filter strategy

The NTP offset formula is exact only when the network delay is
symmetric; when it is not, the error is roughly half the asymmetry.
The total RTT bounds the possible asymmetry, so a low-RTT sample
carries a tighter error bound than a high-RTT one. The filter
therefore keeps a window of recent samples, sorts by RTT, takes the
median offset of the K lowest, and refuses to apply anything until K
samples have been accepted. This is NTP's `clock_filter` insight; NTP
uses K=1, we use K=3 so one unlucky sample cannot dominate. Tuning
constants (window size, K, RTT gate, lease, intervals) live in
`clock_sync_client.cpp`.

## Ping schedule

A fresh target triggers a burst (one ping per 500 ms, up to 10 s) so
the filter window fills and the first lease lands within seconds of
attachment instead of waiting out the steady cadence. After the first
applied lease the client drops to one round per 15 s, which renews the
55 s lease with headroom for one missed renewal.

## Controller reboots

The device remembers the `controller_boot_token` from the first
matched PONG. A change in that value means the controller restarted on
the same address: a fresh monotonic clock that the old offset and old
samples say nothing about. The device clears `SyncedClock`, resets the
filter, discards the triggering PONG (it witnesses the change; it is
not a measurement against the new epoch), and re-enters the burst.

A target IP change or loss resets the filter but leaves `SyncedClock`
alone: the lease is the policy for "is this offset still trustworthy",
and a transient link drop does not invalidate the math. The remembered
token rides along for the same reason; offset and epoch encode one
fact and expire together.

## The controller's sync server

What the Python side must implement, all of it stateless per packet:

- Listen on UDP 6043. For each valid `PING`, reply with a unicast
  `PONG` to the packet's source address.
- Stamp `t2` as close to receipt and `t3` as close to send as
  practical, from the same monotonic microsecond clock that timestamps
  `program_start_us` in Start/Resume commands; that clock is the
  canonical session time devices sync to. Server processing time
  cancels out of the offset and RTT formulas, so reply promptness
  affects nothing.
- Echo `seq` and `t1` verbatim; the device uses them to match the
  reply to its one in-flight round.
- Send a `controller_boot_token` generated once at process start and
  constant for the process lifetime. It must be non-zero (0 is the
  device's unseeded sentinel); regenerate a zero draw.
- Use `uid` as the demux key; `(src_ip, src_port)` is not a stable
  per-device identity (sims share 127.0.0.1, DHCP churns, ephemeral
  ports rotate). Use `device_boot_token` as an admission check
  against the accepted session for that UID; on mismatch discard the
  packet, never mutate attachment state.

The sim exercises this protocol for real on every run: same client
code, controller on the same host, measured offset ~0 because both
sides read the same kernel clock.

## Why this shape

Device-initiated follows v3's connection model (the device connects
out; the
controller does not know where to ping until the device has already
found it). Device-computed keeps the math where the timestamps
originate, halves the wire traffic per round, and makes lease issuance
a device-side constant. The cost is that the controller has no
per-device offset/RTT telemetry; see open items.

## Open items

- **Sync status push** so the operator dashboard is not dark; probably
  extra fields in the `QueryDeviceStatus` ACK, not a new opcode.
- **Schedule jitter at scale.** Add a small random offset (±2 s) to
  steady-mode pings if a single LAN ever carries more than ~20
  devices, so rounds do not synchronize into contention spikes.
- **Tuning revisits.** Lease length (55 s, carried from v2), burst
  spacing (500 ms / 10 s deadline) if the window does not fill on real
  Wi-Fi.

---

## Appendix A: sync UDP packets (port 6043)

**PING** (device → controller):

| `u8 type=0x01` | `char uid[16]` | `u32 device_boot_token` | `u32 seq` | `i64 t1_us` |

Total: 33 bytes. `uid` is null-padded ASCII.

**PONG** (controller → device, unicast reply):

| `u8 type=0x02` | `u32 controller_boot_token` | `u32 seq` | `i64 t1_us` | `i64 t2_us` | `i64 t3_us` |

Total: 33 bytes. Multi-byte fields are little-endian.
