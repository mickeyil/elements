# SyncedClock: Sync Policy, Wire Format, and Integration

Controller-side sync policy, wire format, and firmware integration for the
`SyncedClock` abstraction. The firmware-side API lives in
`drafts/synced_clock.h`.

## Model

Each sync update is a **lease**: an offset and a validity duration.

- The controller samples RTT to the device, filters offsets, and periodically
  emits a lease.
- The firmware applies the lease and trusts the remote-time estimate until
  the lease expires.
- No drift estimation or outlier filtering on the firmware side. The
  controller owns sync quality.
- Every successful sync round on the controller emits exactly one wire
  message. The deadband and sign-flip defer branches no longer suppress the
  wire message; they only suppress the confirm-probe burst.

## Wire Format

Command: `CMD_SYNC_LEASE` (replaces the legacy `CMD_SYNC_RESULT`).

Payload:

| Field          | Type | Units | Notes                                      |
|----------------|------|-------|--------------------------------------------|
| `seq`          | u16  |       | latest probe processed; used for firmware ordering/freshness gating, not necessarily the probe that produced `offset_us` |
| `boot_token`   | u32  |       | firmware boot identifier                   |
| `offset_us`    | i64  | µs    | local clock minus remote clock             |
| `valid_for_ms` | u32  | ms    | lease duration, anchored at firmware receipt time |

Controller encoder: `encode_sync_lease(seq, boot_token, offset_us, valid_for_ms)`
in `controller/elemctl/device_protocol.py`. Update the docstring to reflect
the lease semantics — the message is a *current-sync snapshot*, not an
imperative correction.

## Controller Constants

Add to `controller/elemctl/clock_sync.py`:

- `SYNC_LEASE_MS = 55_000` — lease duration. Satisfies:
  `steady probe 15s  <  renewal 15s  ≤  lease 55s  <  stale 60s`.
  Survives one missed renewal comfortably; usually survives two. Firmware
  expires before the controller gives up, which is the safe ordering.

Existing constants still in use (unchanged):

- `_STEADY_INTERVAL_NS = 15s` — probe cadence, now also lease-renewal cadence.
- `_STALE_TIMEOUT_NS = 60s` — controller stops sending if no sample arrives.
- `_CORRECTION_DEADBAND_US = 2ms` — gates real corrections only; does not
  gate wire emission.

The tolerance concept (previously "MAX_SYNCED_OFFSET_US = 20ms") is a
controller-only policy knob that drives the derivation of `SYNC_LEASE_MS`.
Firmware does not see it.

## `SyncUpdate` Dataclass Rename

In `clock_sync.py`:

| Old                          | New                  | Meaning                              |
|------------------------------|----------------------|--------------------------------------|
| `send_correction`            | `send_sync_lease`    | emit `CMD_SYNC_LEASE` on the wire    |
| `correction_offset_us`       | `lease_offset_us`    | offset value carried on the wire     |
| —                            | `is_new_correction`  | triggers confirm-probe burst         |
| —                            | `valid_for_ms`       | lease duration for this update       |

`send_sync_lease` is a wire-emission flag. `is_new_correction` is an internal
scheduling flag. They are now independent.

Lease delivery to firmware and status reporting to clients are separate
controller outputs: lease renewals must not depend on whether a status
event is emitted. Status updates fire when a user-visible sync state or
value meaningfully changes; steady deadband renewals emit only a wire
lease, not a status event.

## Controller Branches

In `_process_round_samples`, every successful round returns
`send_sync_lease=True`. `is_new_correction` distinguishes lease refreshes
from real corrections.

- **Deadband hit** (`|residual| < _CORRECTION_DEADBAND_US`):
  - `is_new_correction = False`
  - `lease_offset_us = state.applied_offset_us`
  - `valid_for_ms = SYNC_LEASE_MS`
  - no confirm-probe burst.
- **Sign-flip defer** (new residual sign != `prev_delta_sign`):
  - same shape as deadband — lease refresh only, no burst.
- **Real correction** (same-sign residual past deadband, or first correction):
  - `is_new_correction = True`
  - `lease_offset_us = smoothed_offset_us`
  - `valid_for_ms = SYNC_LEASE_MS`
  - call `_schedule_confirm_probes(state, now_ns)` as today.

## Stale Recovery

In `_collect_stale_updates`, when a device is flipped to `'stale'`, also
discard correction history:

- `applied_offset_us = None`
- `latest_smoothed_offset_us = None`
- `residual_offset_us = None`
- `prev_delta_sign = 0`
- `last_correction_ns` is telemetry-only — leave as is.

The first good smoothed value after recovery then takes the
`applied_offset_us is None` branch — a real correction with a fresh lease
and confirm burst. This avoids reviving a pre-stale lease.

## Firmware Integration

In `src/firmware/controller_connection.{h,cpp}`:

- `CMD_SYNC_RESULT` → `CMD_SYNC_LEASE`.
- `handle_sync_result_` → `handle_sync_lease_`.
- Parse the additional `valid_for_ms` field.
- Call `_clock.apply_sync_offset(offset_us, int64_t(valid_for_ms) * 1000)`.
- `boot_token` and `seq` gating unchanged.
- Wires directly into `SyncedClock`; `PlaybackDevice` is not in the path.

Dead code removed from `PlaybackDevice`:

- `handle_sync_result()`
- `clear_sync()`
- fields: `_sync_offset`, `_sync_valid`, `_playback_uses_sync`

`SyncedClock::clear_sync()` is called from the controller-connection layer
on detach/disconnect, not from playback.

## Push-Revoke

A controller that wants to force firmware unsynced without dropping the TCP
session sends `CMD_SYNC_LEASE` with `valid_for_ms = 0`. Under the
strict-less-than check in `is_synced()`, this naturally yields an
immediately-expired lease. No special case in firmware.

## What Is Not Changing

- UDP probe/response path: `CMD_SYNC_REQ`, `SYNC_RESP`, `parse_sync_resp`.
  These measure RTT and feed the controller's filter; they are not leases.
- Controller filtering: 3-best-by-RTT per round, 5-round median window, RTT
  gate (`0 < rtt ≤ 200ms`), deadband value, sign-flip hysteresis.
- Boot token and seq gating on the firmware side.

## Test Seam (Principle)

Deterministic tests control the platform raw-time source — not `SyncedClock`
or `Playback` through virtual or callback APIs. This keeps each as a single
concrete production class while still allowing lease-expiry and
render-cursor behavior to be exercised without real sleep. Implementation
details are out of scope for this doc.
