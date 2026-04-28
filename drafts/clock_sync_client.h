#pragma once

#include <cstddef>
#include <cstdint>

#include "device_hello.h"

class SyncedClock;

namespace controller_link {

class UdpTransport;
class ControllerLink;

// Device-side feeder for SyncedClock. Owns the UDP socket on the sync
// port, the ping schedule, and the small filter that turns RTT samples
// into an offset estimate. Calls SyncedClock::apply_sync_offset() with
// each accepted update.
//
// Producer / shared state / consumers:
//   ClockSyncClient  -- producer; writes to SyncedClock via apply_sync_offset()
//   SyncedClock      -- shared state; standalone, anyone can read
//   Playback (etc.)  -- consumers; read SyncedClock to drive timing
// All three are owned by the App at the same level. ClockSyncClient
// holds a SyncedClock reference for the write channel, but does NOT
// wrap or own the clock -- that would hide shared state behind a
// networking object and couple the two unnecessarily.
//
// Polling contract (see drafts/controller_link.md): the App calls
// poll() unconditionally each tick. The client self-gates internally:
//   - When link.is_ready() goes from true to false in a single tick,
//     poll() resets the filter window (so a stale pre-disconnect offset
//     doesn't blend with a fresh post-reconnect measurement) and calls
//     SyncedClock::clear_sync() so consumers see the unsynced flip
//     immediately rather than waiting for the lease to age out.
//   - When link.is_ready() is false: poll() is a no-op.
//   - When ready: send pings on schedule, drain responses, feed the
//     filter, write SyncedClock once enough samples have accumulated.
//
// The client owns the clear_sync() call because it's the producer that
// just noticed its writes have stopped being meaningful. Asking the
// App to coordinate that would be needless orchestration.
//
// Sim parity: same code on ESP and sim. Sim runs against a controller
// process on the same host, so the measured offset is ~0; that's not
// faked, it's the truth. The sync wire is exercised on every sim run.
//
// See drafts/synced_clock.md for the wire flow, filter rationale, and
// the open device-computes vs controller-computes decision.

class ClockSyncClient {
public:
    ClockSyncClient(
        UdpTransport&         udp,
        const ControllerLink& link,
        SyncedClock&          clock,
        const DeviceIdentity& identity
    );

    // Single per-tick entry point. Called unconditionally by the App
    // (no outer is_up()/is_ready() guard). Self-gates: no-op when
    // !link.is_ready(); on the tick where link readiness drops it
    // resets internal filter state and calls SyncedClock::clear_sync().
    void poll();

    // WINDOW_N visible here so the embedded sample buffer can be
    // sized at compile time without exposing the whole tuning block.
    static constexpr size_t WINDOW_N = 5;

private:
    // One accepted RTT sample. The filter sorts the window by
    // rtt_us ascending and takes the K lowest.
    struct Sample {
        // Controller clock minus device clock, microseconds, computed
        // by the NTP four-timestamp formula. Stored in this sign so
        // the median is taken in a natural direction; negated when
        // written to SyncedClock (which stores local - remote).
        int64_t remote_minus_local_us;

        // Network-only round-trip time in microseconds:
        // (t4 - t1) - (t3 - t2). Used as the trust ranking.
        int64_t rtt_us;
    };

    // Called when poll() observes link.is_ready() going from true
    // to false. Wipes filter window, outstanding round, and burst
    // counter; calls SyncedClock::clear_sync().
    void on_link_down_();

    // True iff the next ping is due. Initializes the schedule on
    // the first call after the link became ready.
    bool schedule_due_();

    // Build and send a PING. State commits (round outstanding, seq,
    // t1, next-ping schedule) happen only after the send succeeds;
    // a failed send marks the socket unbound and backs off briefly.
    void send_ping_();

    // Close the UDP socket and clear _bound. Idempotent. Used on
    // link-down and on socket errors so the next poll() re-binds
    // cleanly (rather than asking the transport to re-bind on top
    // of an already-bound state, which the udp_transport contract
    // doesn't promise to handle for ephemeral ports).
    void mark_unbound_();

    // Drain all available PONGs from the UDP socket. For each:
    // validate framing, validate it matches the outstanding round,
    // call process_round_().
    void drain_responses_();

    // Apply NTP four-timestamp formula, gate by RTT, push into
    // window, run apply_filter_() if enough samples.
    void process_round_(int64_t t1, int64_t t2, int64_t t3, int64_t t4);

    // Best-K-of-N by RTT, median of those K offsets, write to
    // SyncedClock with a fresh lease.
    void apply_filter_();

    // Local monotonic clock in microseconds. Wrapped for testability;
    // production impl is platform_clock::now_us().
    int64_t now_us_() const;

    UdpTransport&         _udp;
    const ControllerLink& _link;
    SyncedClock&          _clock;
    const DeviceIdentity& _identity;

    // Tracks whether the link was ready last tick, so poll() can
    // detect the moment readiness drops and run the disconnect
    // handler exactly once.
    bool _was_ready = false;

    // True iff the UDP socket is currently bound and usable. The
    // socket is bound lazily inside poll() the first time the link
    // becomes ready -- a constructor-time bind can fail when the
    // network isn't up yet, and we'd have no good way to recover.
    // A socket error during recv clears this so the next tick
    // re-binds.
    bool _bound = false;

    // Ping schedule. _next_ping_due_us is the local timestamp at
    // which send_ping_() should next fire; _bursts_remaining counts
    // down the initial fast-fill series.
    int64_t _next_ping_due_us = 0;
    size_t  _bursts_remaining = 0;

    // Filter window. Bounded ring of WINDOW_N entries. _samples_count
    // is the live entry count (<= WINDOW_N). Samples are kept in
    // arrival order; the filter sorts copies it doesn't mutate the
    // window in place.
    Sample _samples[WINDOW_N];
    size_t _samples_count = 0;

    // Outstanding-round bookkeeping. Only one round is outstanding
    // at a time; a second ping doesn't fire until the previous round
    // completes or times out.
    bool     _round_outstanding = false;
    uint32_t _round_seq         = 0;
    int64_t  _round_t1_us       = 0;

    // Monotonic sequence number on PINGs. Wraps; PONG matches by
    // exact equality against _round_seq.
    uint32_t _seq = 0;
};

}  // namespace controller_link
