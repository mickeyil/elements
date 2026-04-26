#pragma once

// Concrete synced-clock abstraction.
//
// SyncedClock publishes two time domains:
// - local time: the device's own monotonic clock in microseconds.
// - remote time: an estimate of the shared/authoritative clock in
//   microseconds, derived from the local clock and the most recently
//   applied sync offset.
//
// Sync offsets are supplied with a lease duration (valid_for_us). is_synced()
// reports whether the current lease is still valid. When the lease has
// expired, or when no offset has been applied since construction or the last
// clear_sync(), remote time is considered untrustworthy.
//
// Remote time is not guaranteed monotonic: when a new offset is applied, the
// estimate can step forward or backward. Callers that require monotonic
// remote time must enforce that invariant themselves.
//
// Sign convention for the offset:
//   offset_us == now_local_us() - now_remote_us()
// Positive means the local clock leads remote.
//
// Platform support. SyncedClock is a single concrete class with no virtuals,
// templates, or callbacks. The raw monotonic source is reached through
// platform_clock::now_us(); link-time selection picks the host clock, the
// ESP timer, or the test fake. Deterministic tests substitute the platform
// raw-time source, not SyncedClock itself.

#include <cassert>
#include <cstdint>

#include "platform_clock.h"

class SyncedClock {
public:
    // True iff an offset has been applied and its lease has not yet expired.
    bool is_synced() const {
        return _has_offset && platform_clock::now_us() < _valid_until_local_us;
    }

    // Current remote-clock estimate in microseconds, computed as
    // `now_local_us() - _offset_us`. Returns a value even after the lease
    // has expired; callers that require a fresh estimate gate on is_synced()
    // first. After clear_sync(), returns now_local_us() (the offset is
    // wiped).
    int64_t now_remote_us() const {
        return platform_clock::now_us() - _offset_us;
    }

    // Current local monotonic clock in microseconds.
    int64_t now_local_us() const {
        return platform_clock::now_us();
    }

    // Apply a new sync offset with a validity window.
    //
    // offset_us    : local clock minus remote clock, in microseconds
    //                (positive = local leads remote).
    // valid_for_us : lease duration measured from the current local time.
    //                Zero produces an immediately expired lease (push-revoke);
    //                a subsequent is_synced() returns false. Must be >= 0:
    //                negative values are not produced by the wire protocol
    //                (u32 ms) and are not part of the contract.
    void apply_sync_offset(int64_t offset_us, int64_t valid_for_us) {
        assert(valid_for_us >= 0);
        _offset_us = offset_us;
        _valid_until_local_us = platform_clock::now_us() + valid_for_us;
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
