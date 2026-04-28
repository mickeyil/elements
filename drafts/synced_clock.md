# Clock Sync (v3)

How the device keeps `SyncedClock` fresh against the controller in v3.
The big shift from v2: the **device initiates** sync instead of
responding to it, and sync runs on its **own UDP port** instead of
piggybacking on discovery (UDP) and TCP (`CMD_SYNC_LEASE`/`CMD_SYNC_RESULT`).

`SyncedClock` itself is unchanged — it's still the passive container in
`src/synced_clock.h` that holds an offset and a validity window and
answers `is_synced()` / `now_remote_us()`. What's new is the feeder:
**`ClockSyncClient`** (device-side, sibling to `SyncedClock` and to
`ControllerLink`).

## Where it sits

`ClockSyncClient` is the third sibling alongside `NetworkInterface` and
`ControllerLink` (see `drafts/controller_link.md`). The App ticks it
each loop alongside the link:

```
network.poll();
if (network.is_up()) {
    link.poll();
    sync.poll();   // no-op when !link.is_ready(); resets on the down-edge
}
```

`sync.poll()` reads `link.is_ready()` and `link.controller_ip_addr()`
to know whether to ping and where to ping. When the link drops, the
client wipes its filter window so a stale pre-detach offset doesn't
blend with a fresh post-attach measurement. `SyncedClock`'s lease will
expire on its own; the App can also call `clock.clear_sync()` on
disconnect for an immediate flip.

## Why the direction flipped

In v2, the controller pings, the device responds with timestamps, and
the controller computes the offset and pushes it back over TCP as
`CMD_SYNC_LEASE`. That made sense when the controller was the
"server-shaped" side of the link. With v3 inverting the connection
model (device dials), making the controller the active sync initiator
becomes structurally weird — the controller doesn't know where to ping
until the device has discovered it, and once the device has discovered
it, the device is the one doing things.

So sync follows the same direction as the rest of v3: device-initiated.

## Sim parity

Same code on ESP and sim. The sim runs `ClockSyncClient` against a
controller process on the same host; the measured offset is ~0 because
the local clock and the controller's clock are literally the same
kernel clock. That's the truth, not a stub. The sync wire protocol
gets exercised on every sim run, so framing or lease bugs are catchable
on a laptop instead of only on hardware.

There is no sim-only stub that "skips" sync. If a future setup runs the
controller on a different host and the sim against it, the same client
measures the real offset and feeds `SyncedClock` the same way. No
divergence to maintain.

## Wire flow

The wire flow depends on the open device-vs-controller-computes
decision (next section). Both options share the first two messages:

```
device → controller   PING   uid + boot_token + seq + t1
controller → device   PONG   echoes uid + seq + t1, adds t2 + t3
```

The device can compute offset and RTT from `(t1, t2, t3, t4)` where
`t4` is its receive time. From there, the two options diverge in
whether the device or the controller does the smoothing and lease
issuance.

`uid` and `boot_token` are in every device-to-controller packet for
the same reason the OFFER nonce is in `DEVICE_HELLO`: between
`is_ready()` becoming true and the controller actually accepting the
attachment, the device may briefly send pings to a controller that's
about to close. The controller validates the UID/boot_token and
discards stale ones. This costs ~20 bytes per ping and removes a small
race window.

Cadence: roughly one round per 15 seconds in steady state (matches
v2's `_STEADY_INTERVAL_NS`), with a faster burst right after the link
becomes Ready so the lease is established before the first synced
program is loaded.

## Open: device-computes vs controller-computes

This is the live decision. Both work; the trade is wire complexity
against device-side code complexity and operator visibility.

**Device computes (2 UDP messages per round)**

The device runs the standard NTP four-timestamp formula on its own
samples, keeps a small ring buffer (last N), discards outliers by
RTT, takes the median, and calls `SyncedClock::apply_sync_offset` with
a self-issued lease window. Filter is ~40 lines of platform-agnostic
C++.

- Half the wire traffic per round — and a dropped UDP packet kills
  one round, not the report leg of every round.
- The math lives where the timestamps originate; no need to ship them
  back to the controller and wait.
- Lease validity is a device-side constant (e.g., 55 s, same as the
  v2 `SYNC_LEASE_MS`).
- Cost: the controller no longer sees per-device offset/RTT
  telemetry. If the operator dashboard wants that, the device has to
  push its sync status back over TCP — either folded into
  `QueryDeviceStatus` or as its own opcode.

**Controller computes (4 UDP messages per round)**

The device sends PING, receives PONG with `(t2, t3)`, then sends a
REPORT with its `t4`, and the controller replies with a LEASE
containing `(offset_us, valid_for_ms)` after smoothing. The device
just calls `apply_sync_offset` with whatever the controller said.

- Centralized policy: the controller already has
  `_CORRECTION_DEADBAND_US`, sign-flip hysteresis, and the per-device
  state machine in `controller/elemctl/clock_sync.py` from v2; that
  code mostly survives.
- Per-device telemetry stays on the controller for free.
- Cost: 4 messages per round, larger blast radius from a single
  packet loss, more wire format to specify, and an opcode-shaped
  thing on UDP.

**Lean.** Device-computes is the cleaner shape: the math is small,
data flows in one direction, and the controller stops being an
oracle for something the device can compute itself. The deciding
factor is whether the operator will actually look at per-device sync
telemetry — if yes, the small "device → controller sync status" push
is worth it; if no, device-computes wins outright.

## What `ClockSyncClient` does each tick

Independent of the device-vs-controller decision:

1. If `!link.is_ready()`: reset the filter window and any in-flight
   round state. Return.
2. If a round is due (no current round in flight, cadence interval
   elapsed): build a PING with current timestamp, UID, boot_token,
   seq; `udp.send(...)` to `link.controller_ip_addr()` on the sync
   port.
3. Drain `udp.recv(...)`. For each packet: validate it's a PONG (or
   LEASE under controller-computes), validate UID/boot_token/seq,
   feed into the filter (device-computes) or apply the lease
   directly (controller-computes).
4. If a fresh accepted offset is ready, call
   `clock.apply_sync_offset(offset_us, lease_us)`.

Cadence and budget are local concerns: the client owns its own
schedule, doesn't share state with the link's discovery cadence.

## Boot/reset behavior

- On simulated reboot, `SimSystemPlatform` writes a fresh `boot_token`
  into the shared `DeviceIdentity`. The next PING carries the new
  token; the controller sees the bump and discards anything it had
  cached for the old boot.
- On link disconnect, `ClockSyncClient::poll()` resets its window. The
  App may also call `SyncedClock::clear_sync()` to flip `is_synced()`
  to false immediately rather than waiting for the lease to age out.
- The "what does playback do when sync is lost mid-program" policy
  belongs to Playback / firmware mode, not to `ClockSyncClient`. See
  `drafts/TODO.md` § Playback (loss-of-sync lifecycle).

## Open decisions

- **Device-computes vs controller-computes** (above).
- **If device-computes wins: a "device → controller sync status"
  push** so the operator dashboard isn't dark. Probably folded into
  `QueryDeviceStatus`'s ACK payload (extra fields), not a new opcode.
- **Lease window length** under device-computes (55 s carries over
  from v2; revisit only if cadence changes).
- **Initial-burst cadence** right after `is_ready()` flips true.
  Probably a few quick rounds (1 s spacing) until the first lease,
  then settle to the steady 15 s cadence.
- **Whether `ClockSyncClient` takes `ControllerLink&` directly** or
  just the two getters it needs. Style choice; `ControllerLink&` is
  what's drafted in `clock_sync_client.h`.

## What is not changing

- `SyncedClock` itself (`src/synced_clock.h`) — passive container,
  stays as-is.
- The high-level "synced program needs a lease before play"
  invariant — `Playback` continues to check `clock.is_synced()` and
  return `Unsynced` for synced START/RESUME/JUMP without one. The
  ACK story is unchanged.
- Boot token semantics — still a fresh u32 generated every (real or
  simulated) boot, included in identity.

---

## Appendix A: sync UDP packets (port 6043)

These are tentative — the exact bytes depend on the device-vs-controller
decision. Listed here as the most likely shape under device-computes.

**PING** (device → controller, ~every 15 s steady, faster initial
burst):

| u8 type=0x01 | char uid[16] | u32 boot_token | u32 seq | i64 t1_us |

Total: 33 bytes.

**PONG** (controller → device, unicast reply):

| u8 type=0x02 | u32 seq | i64 t1_us | i64 t2_us | i64 t3_us |

Total: 29 bytes. `t1_us` is echoed so the device doesn't need to
remember per-seq state if it doesn't want to.

Under controller-computes the same PING/PONG carry the round, plus:

**REPORT** (device → controller):

| u8 type=0x03 | char uid[16] | u32 boot_token | u32 seq | i64 t4_us |

**LEASE** (controller → device):

| u8 type=0x04 | u32 seq | i64 offset_us | u32 valid_for_ms |

Either family fits comfortably in one MTU. Final layout pinned when
the open decision lands.
