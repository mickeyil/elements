#pragma once


// Tracks an estimate of a remote (shared) clock from the local monotonic
// clock and a caller-supplied offset between them.
//
// Typical use: a sync routine measures the offset between the two clocks
// somewhere outside this module, calls apply_sync_offset() with that
// offset and a validity duration, and from then on now_remote_us()
// returns the estimate. Once the validity window passes, is_synced()
// returns false until a fresh offset is applied.
//
//     SyncedClock c;
//     c.apply_sync_offset(offset_us, valid_for_us);
//     if (c.is_synced()) {
//         int64_t t = c.now_remote_us();
//     }

#include <cassert>
#include <cstdint>

#include "platform/platform_clock.h"

class SyncedClock {
public:
    // True iff an offset has been applied and its lease has not yet expired.
    bool is_synced() const {
        return _has_offset && now_us() < _valid_until_local_us;
    }

    // Current remote-clock estimate in microseconds, computed as
    // `now_local_us() - _offset_us`. Returns a value even after the lease
    // has expired; callers that require a fresh estimate gate on is_synced()
    // first. After clear_sync(), returns now_local_us() (the offset is
    // wiped).
    int64_t now_remote_us() const {
        return now_us() - _offset_us;
    }

    // Current local monotonic clock in microseconds.
    int64_t now_local_us() const {
        return now_us();
    }

    // Apply a new sync offset with a validity window.
    //
    // offset_us    : local clock minus remote clock, in microseconds
    //                (positive = local leads remote).
    // valid_for_us : how long this offset is considered valid, in microseconds.
    //                When it elapses, is_synced() returns false. Passing 0
    //                makes the instance immediately unsynced. Must be >= 0.
    void apply_sync_offset(int64_t offset_us, int64_t valid_for_us) {
        assert(valid_for_us >= 0);
        _offset_us = offset_us;
        _valid_until_local_us = now_us() + valid_for_us;
        _has_offset = true;
    }

    // Drop the current offset and mark the clock unsynced.
    void clear_sync() {
        _offset_us = 0;
        _valid_until_local_us = 0;
        _has_offset = false;
    }

private:
    // Last applied offset. Held across lease expiry so now_remote_us()
    // continues to return a last-known mapping.
    int64_t _offset_us = 0;

    // Local-clock timestamp at which the current lease expires.
    int64_t _valid_until_local_us = 0;

    // Whether an offset has been applied since the last clear_sync().
    bool _has_offset = false;
};
