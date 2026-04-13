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
// The exact correction / hysteresis policy is intentionally still open.

#include <cstdint>

class DeviceClock {
public:
    bool is_synced() const;

    int64_t now_controller_us() const;
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
    // TODO: final design should likely separate raw monotonic source access
    // from correction state more explicitly.

    // INVARIANT: _offset_us == local - controller.
    // Positive means local/device time leads controller time.
    int64_t _offset_us = 0;

    // Whether controller-domain time is currently trustworthy.
    bool _is_synced = false;
};
