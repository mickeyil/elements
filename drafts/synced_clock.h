#pragma once

// Draft-only API.
//
// Concrete synced-clock abstraction.
//
// Naming policy:
// - remote time is the shared/remote-owned monotonic time domain
// - local time is the device's own monotonic time domain
// - is_synced() reports whether remote-domain time is currently trustworthy
// - _offset_us == now_local_us() - now_remote_us(); positive means local
//   time leads remote time
//
// `now_remote_us()` is a remote-domain estimate. It may step when a
// fresh sync correction is accepted; Playback owns the monotonic accepted
// `t_program` invariant for rendered animation. The exact sync-health,
// freshness, and large-correction policy is intentionally still open.
//
// Raw monotonic source policy:
// - `SyncedClock` is one concrete class.
// - The raw monotonic source underneath is selected at compile time via
//   `#ifdef ARDUINO`, consistent with the rest of the drafts:
//   - ARDUINO build     -> `esp_timer_get_time()`
//   - non-ARDUINO build -> `std::chrono::steady_clock`
// - No callback injection, no virtual hook: firmware and sim share one
//   concrete `SyncedClock`; only the platform raw source differs.
// - Deterministic Playback tests still need a manual raw-time seam, but that
//   seam is not part of this draft source sketch yet.

#include <cstdint>

class SyncedClock {
public:
    bool is_synced() const;

    // Remote-domain estimate derived from `now_local_us()` minus the
    // current accepted sync offset. This clock is not responsible for clamping
    // rendered program time.
    int64_t now_remote_us() const;

    // Local-domain raw monotonic time.
    // Reads the platform monotonic source directly.
    int64_t now_local_us() const;

    // Apply a fresh sync offset.
    //
    // Sign convention:
    //   local_minus_remote_us = now_local_us() - now_remote_us()
    //
    // Positive means the local/device clock currently leads remote time.
    void apply_sync_offset(int64_t local_minus_remote_us);

    void clear_sync();

private:
    // INVARIANT: _offset_us == local - remote.
    // Positive means local/device time leads remote time.
    int64_t _offset_us = 0;

    // Whether remote-domain time is currently trustworthy.
    bool _is_synced = false;
};
