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
three unconditionally in dependency order each loop, and feeds the
sync target across one explicit line:

```
network.poll();
link.poll();
sync.set_controller(link.controller_ip_addr());  // 0 when !is_ready
sync.poll();
```

`ClockSyncClient` does not depend on `ControllerLink`. Its scope is
exactly clock estimation against a configured peer. Authority — who is
allowed to be the controller, what TCP commands have been accepted —
lives entirely in `ControllerLink`. The bridging line above is the
only place those concerns meet, and it stays in the App.

`controller_ip_addr()` returns 0 when the link is not ready, so the
single-line bridge collapses both branches: setter is idempotent on
unchanged values, and a 0 sentinel goes idle.

Reset triggers are all local to the client:

- **target IP changes** (via `set_controller`): reset filter, drop
  outstanding round, clear the remembered controller boot token,
  restart the burst if the new target is non-zero. `SyncedClock` is
  **not** cleared: the lease is the policy for "is this offset still
  trustworthy", and a transient drop doesn't invalidate the math.
- **`controller_boot_token` in `PONG` changes**: remote clock epoch
  jumped (controller rebooted on the same hardware). Clear
  `SyncedClock`, reset filter, drop outstanding round, discard the
  triggering PONG, restart the burst.

Local reboot is not a reset trigger inside the client: a sim reboot
is a process restart (handled by the launcher; see `drafts/TODO.md`),
which constructs a new `ClockSyncClient` with no carried state. The
`DeviceIdentity` is immutable after construction on both platforms.

The client owns these resets because the signals that trigger them
(IP setter, PONG token field) are observable from inside `poll()`;
no App-side coordination is required beyond the one bridging line.

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

```
device → controller   PING   uid + device_boot_token + seq + t1
controller → device   PONG   controller_boot_token + seq + t1 + t2 + t3
```

The device runs the standard NTP four-timestamp formula on `(t1, t2,
t3, t4)` where `t4` is its receive time. Smoothing and lease issuance
happen entirely on the device (see "Filter strategy" below).

Each field on the wire has one job:

| Field                              | Whose job                                                                         |
|------------------------------------|-----------------------------------------------------------------------------------|
| `uid` (PING)                       | Controller demux key; `(src_ip, src_port)` isn't a stable per-device identity (sim sharing 127.0.0.1; DHCP IP churn; ephemeral port changes on rebind). |
| `device_boot_token` (PING)         | Controller admission check: "is this packet from the currently accepted boot/session for this UID?" Mismatch leads to discard only, never to mutating attachment state. |
| `controller_boot_token` (PONG)     | Device-side remote-epoch identity. First matched PONG seeds the value; subsequent change means the controller rebooted on the same hardware (same IP, fresh monotonic clock). Triggers a clean reset. |
| `seq + t1` (both)                  | Device-side round matching. Echoed in PONG so the device can pair the reply with the outstanding round. |

The PONG carries `controller_boot_token` rather than echoing UID or
the device boot token: the device only listens to one peer (validated
by `src_ip == _target_ip`), so its own identity doesn't need to come
back, but the *remote* identity must, so the device can detect a
controller reboot before the lease drifts.

One round per ~15 seconds during normal operation, with a faster
burst right after a target is configured so the lease is established
before the first synced program is loaded.

## Decided: device-computes

The device runs the NTP four-timestamp formula on its own samples,
keeps a small ring buffer, discards outliers by RTT, takes the
median, and calls `SyncedClock::apply_sync_offset` with a self-issued
lease window. Filter is ~40 lines of platform-agnostic C++.

Why this shape over controller-computes:

- Half the wire traffic per round; a dropped UDP packet kills one
  round, not the report leg of every round.
- The math lives where the timestamps originate; no round trip back
  to the controller for the answer.
- Lease validity is a device-side constant (55 s, matching v2's
  `SYNC_LEASE_MS`).
- Cost: the controller no longer sees per-device offset/RTT
  telemetry. If the operator dashboard wants that, the device pushes
  sync status back over TCP, folded into `QueryDeviceStatus` rather
  than as its own opcode.

## Filter strategy

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

- **Burst on target acquisition.** When `set_controller` receives a
  non-zero IP that differs from the current target, the client enters
  burst mode: cadence drops to 500 ms. Burst exits on the first
  applied lease, or when the burst deadline (10 s) elapses, whichever
  comes first. Without the burst, the first apply could be 30 s after
  the link came up.
- **Normal interval.** Outside burst, one round every ~15 s. Renews
  the lease (~55 s) with comfortable headroom for one missed renewal.
- **Schedule jitter at scale.** With more than ~20 devices on the
  same LAN, add a small random offset (e.g., ±2 s) to ping times so
  devices don't end up sending at the same instants and producing
  periodic contention spikes on the controller's Wi-Fi. Open item;
  not implemented in the first cut.

## Boot/reset behavior

The reset triggers and what each one invalidates:

| Trigger                                            | Filter | Outstanding round | UDP socket | `_last_controller_boot_token` | `SyncedClock`               |
|----------------------------------------------------|--------|-------------------|------------|-------------------------------|-----------------------------|
| `set_controller(0)` (target cleared)               | reset  | dropped           | closed     | rides with lease              | left alone (lease rides)    |
| `set_controller(new_ip)` (target changed)          | reset  | dropped           | closed     | rides with lease              | left alone (lease rides)    |
| Remote `controller_boot_token` change in PONG      | reset  | dropped           | left bound | updated to new value          | **cleared**                 |
| `recv` / `send` socket error                       | left   | left              | closed     | left alone                    | left alone                  |

A local (device) reboot is not a row in this table because it isn't
observed in-process: on ESP it's `ESP.restart()`, on sim it's
`_exit(SIM_REBOOT_EXIT_CODE)` followed by a fresh launcher-driven
process. Either way the next `ClockSyncClient` is constructed from
scratch.

The token rides through `set_controller(0)` and target IP changes for
the same reason `SyncedClock`'s lease does: the two encode a single
fact ("this is the offset, measured against this remote epoch"), and
splitting them lets a same-IP-different-epoch reconnect within the
lease window silently re-seed against a new controller's clock
without ever detecting the change.

Notes:

- The UDP socket binds lazily inside `poll()` on the first tick a
  target is set; a bind failure just delays the first ping to the
  next tick rather than stranding the client.
- On a remote epoch change the triggering PONG is **discarded** (not
  processed as a sample): it's the witness of change, not a usable
  measurement against the new epoch. The next ping fires immediately
  and starts the new epoch's first round.
- Validation order on incoming PONGs is round-match first, then
  token check. A stale or duplicate PONG that doesn't match the
  current `(seq, t1)` is discarded silently and cannot trigger a
  spurious epoch reset, regardless of what token it carries. The
  token check fires only on a PONG that round-matches our most
  recent PING; that's the only PONG fresh enough to be evidence of
  a real epoch change.
- Closing the UDP socket to flush the kernel queue on token change
  is unnecessary: stale queued PONGs from before the reboot all fail
  the round-match check (their seq is older than `_last_sent_seq`).
- The "what does playback do when sync is lost mid-program" policy
  belongs to Playback / firmware mode, not to `ClockSyncClient`. See
  `drafts/TODO.md` § Playback (loss-of-sync lifecycle).

## Open decisions

- **Device → controller sync status push** so the operator dashboard
  isn't dark. Probably folded into `QueryDeviceStatus`'s ACK payload
  (extra fields), not a new opcode.
- **Lease window length** (55 s carries over from v2; revisit only if
  the ping interval changes).
- **Initial-burst spacing.** Currently 500 ms with a 10 s deadline;
  revisit if the window doesn't fill in time on real Wi-Fi.
- **Schedule jitter at scale.** Add ±2 s random offset to steady-mode
  pings if the fleet ever crosses ~20 devices per LAN.

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

**PING** (device → controller, ~every 15 s steady, faster initial
burst):

| `u8 type=0x01` | `char uid[16]` | `u32 device_boot_token` | `u32 seq` | `i64 t1_us` |

Total: 33 bytes.

**PONG** (controller → device, unicast reply):

| `u8 type=0x02` | `u32 controller_boot_token` | `u32 seq` | `i64 t1_us` | `i64 t2_us` | `i64 t3_us` |

Total: 33 bytes. `controller_boot_token` is generated by the
controller process at startup; the device uses changes in this value
to detect a controller reboot on the same hardware.

`controller_boot_token` **must be non-zero**. The value 0 is reserved
on the device side as the unseeded sentinel (`_last_controller_boot_token`
starts at 0 before the first matched PONG seeds it). Controller
implementations that draw the token from a random source must
regenerate any zero result, mirroring how the device handles its own
`boot_token` (`src/firmware/device_identity.cpp:14`). The device
silently treats a zero-token PONG as "still unseeded" and never
detects a token change against it.

`t1_us` is echoed so the device matches replies to the outstanding
round without keeping per-seq state beyond a single in-flight round.
