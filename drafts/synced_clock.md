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
`ControllerLink` (see `drafts/controller_link.md`). The App ticks all
three unconditionally in dependency order each loop:

```
network.poll();
link.poll();
sync.poll();
```

There's no `if (network.is_up())` guard. The polling contract (see
`controller_link.md` § "The polling contract") is that each module
self-gates and handles prerequisite loss. For the sync client that
means:

- `sync.poll()` reads `link.is_ready()` and `link.controller_ip_addr()`
  internally to decide whether to ping and where to ping.
- On the tick where `link.is_ready()` goes from true to false, the
  client resets its filter window (so a stale pre-disconnect offset
  doesn't blend with a fresh post-reconnect measurement) and calls
  `SyncedClock::clear_sync()` so consumers see the unsynced flip
  immediately rather than waiting for the lease to age out.

The client owns the `clear_sync()` call because it's the producer that
just noticed its writes have stopped being meaningful — wiping the
consumer-visible state is a derivable consequence of detach, not
something the App should have to coordinate.

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

## Filter strategy (device-computes path)

The device runs the standard NTP four-timestamp formula on each
PING/PONG round and feeds the resulting `(offset, RTT)` pair into a
small ring-buffer filter. The filter trusts samples with lower RTT
more than samples with higher RTT, rather than averaging across the
whole window equally.

The reason traces back to the offset formula's hidden assumption:
it's exact only if the one-way network delay is the same outbound and
inbound. When that's wrong, the offset estimate is wrong by
roughly half the asymmetry. The total RTT bounds how big the
asymmetry can possibly be — so a sample with low RTT has a tighter
bound on its offset error than a sample with high RTT.

Concretely: each fresh sample is added to a window of recent
samples; the filter sorts that window by RTT, keeps the K with
lowest RTT, and takes the median of their offsets. That median goes
into `SyncedClock::apply_sync_offset` with a fresh lease.

Three rules govern when the filter actually fires:

- **RTT gate.** A sample with negative RTT (clock anomaly) or RTT
  above a sanity threshold is dropped before it ever enters the
  window.
- **Minimum samples before first apply.** No `apply_sync_offset`
  happens until the window holds at least K accepted samples. This
  is what the burst-at-attach is for — it fills the window quickly
  so the first synced program doesn't have to wait for the normal
  ping interval.
- **One apply per accepted sample.** Each new accepted sample
  re-runs the filter and re-issues a lease, even if the offset
  changed by very little. Lease renewal and real corrections share
  the same code path.

This is the same insight NTP uses in its `clock_filter`. NTP itself
takes K=1 (just the single lowest-RTT sample); we use K=3 so a
single unlucky-but-low-RTT sample doesn't dominate.

Tuning (`WINDOW_N`, `BEST_K_BY_RTT`, `MIN_SAMPLES_TO_APPLY`,
`RTT_GATE_US`, lease, ping interval) lives in the `.cpp` as
`constexpr`s. The defaults aim at typical Wi-Fi LAN behavior with
~5–30 ms RTT during normal operation and occasional spikes.

## Ping schedule

- **Burst at link-up.** When `link.is_ready()` first flips true, the
  client sends a short series of pings ~500 ms apart to fill the
  filter window quickly. Without the burst, the first apply could be
  30–45 s after the link comes up.
- **Normal interval.** After the burst, one round every ~15 s.
  Renews the lease (~55 s) with comfortable headroom for one missed
  renewal.
- **Schedule jitter at scale.** With more than ~20 devices on the
  same LAN, add a small random offset (e.g., ±2 s) to ping times so
  devices don't end up sending at the same instants and producing
  periodic contention spikes on the controller's Wi-Fi.

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
  from v2; revisit only if the ping interval changes).
- **Initial-burst spacing** right after `is_ready()` flips true.
  Currently 500 ms × 5 pings; revisit if the window doesn't fill in
  time on real Wi-Fi.
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
