#pragma once

// Draft-only API.
//
// Concrete shared clock abstraction for synced vs unsynced playback time.
// The exact correction / hysteresis policy is intentionally still open.

#include <cstdint>

class SyncedClock {
public:
    bool is_synced() const;
    int64_t now_synced_us() const;
    int64_t now_unsynced_us() const;

    void apply_correction(int64_t offset_us);
    void clear_sync();

private:
    // TODO: final design should likely separate raw monotonic source access
    // from correction state more explicitly.

    // Last applied controller-relative correction.
    int64_t _offset_us = 0;

    // Whether the clock currently considers its correction trustworthy.
    bool _is_synced = false;
};
