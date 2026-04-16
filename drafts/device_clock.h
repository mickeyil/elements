#pragma once

// Draft-only API.
//
// Concrete device-side clock abstraction.
//
// Naming policy:
// - controller time is the shared/controller-owned monotonic time domain
// - local time is the device's own monotonic time domain
// - is_synced() reports whether controller-domain time is currently trustworthy
// - _offset_us == now_local_us() - now_controller_us(); positive means local
//   time leads controller time
//
// `now_controller_us()` is a controller-domain estimate. It may step when a
// fresh sync correction is accepted; Playback owns the monotonic accepted
// `t_program` invariant for rendered animation. The exact sync-health,
// freshness, and large-correction policy is intentionally still open.
//
// Raw monotonic source policy:
// - `DeviceClock` is one concrete class.
// - The raw monotonic source underneath is selected at compile time via
//   `#ifdef ARDUINO`, consistent with the rest of the drafts:
//   - ARDUINO build     -> `esp_timer_get_time()`
//   - non-ARDUINO build -> `std::chrono::steady_clock`
// - No callback injection, no virtual hook: firmware and sim share one
//   concrete `DeviceClock`; only the platform raw source differs.
// - Deterministic Playback tests still need a manual raw-time seam, but that
//   seam is not part of this draft source sketch yet.

#include <cstdint>

class DeviceClock {
public:
    bool is_synced() const;

    // Controller-domain estimate derived from `now_local_us()` minus the
    // current accepted sync offset. This clock is not responsible for clamping
    // rendered program time.
    int64_t now_controller_us() const;

    // Local-domain raw monotonic time.
    // Reads the platform monotonic source directly.
    int64_t now_local_us() const;

    // Apply a fresh sync offset.
    //
    // Sign convention:
    //   local_minus_controller_us = now_local_us() - now_controller_us()
    //
    // Positive means the local/device clock currently leads controller time.
    void apply_sync_offset(int64_t local_minus_controller_us);

    void clear_sync();

private:
    // INVARIANT: _offset_us == local - controller.
    // Positive means local/device time leads controller time.
    int64_t _offset_us = 0;

    // Whether controller-domain time is currently trustworthy.
    bool _is_synced = false;
};
