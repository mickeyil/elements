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
//   ClockSyncClient: producer; writes to SyncedClock via apply_sync_offset()
//   SyncedClock:     shared state; standalone, anyone can read
//   Playback (etc.): consumers; read SyncedClock to drive timing
// All three are owned by the App at the same level. ClockSyncClient
// holds a SyncedClock reference for the write channel, but does NOT
// wrap or own the clock; that would hide shared state behind a
// networking object and couple the two unnecessarily.
//
// poll() runs every tick. What it does depends on the link:
//   - link not ready: do nothing.
//   - link just became not-ready: reset the filter, throw away the
//     in-flight ping, close the socket. SyncedClock is left alone
//     so its offset stays usable until its lease runs out.
//   - link ready: send pings on schedule, read replies, write the
//     measured offset to SyncedClock once enough samples are in.
// See drafts/controller_link.md for why poll() runs every tick.

// One accepted sync round. Filter ranks by rtt_us ascending.
struct SyncSample {
    int64_t offset_us;  // remote minus local time, in us
    int64_t rtt_us;     // network round-trip, in us
};

class ClockSyncClient {
public:
    ClockSyncClient(UdpTransport& udp, const ControllerLink& link,
                    SyncedClock& clock, const DeviceIdentity& identity);

    // Called every tick. Sends pings and processes replies when the
    // link is ready; does nothing otherwise.
    void poll();

    // Filter window size.
    static constexpr size_t WINDOW_N = 5;

private:
    // Called on the link-down transition. Resets sync state;
    // SyncedClock keeps its offset until its lease expires.
    void on_link_down_();

    // True iff the next ping is due. Initializes the schedule on
    // the first call after the link became ready.
    bool schedule_due_();

    // Build and send a PING. State commits (round outstanding, seq,
    // t1, next-ping schedule) happen only after the send succeeds;
    // a failed send closes the socket and backs off briefly.
    void send_ping_();

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

    // Ping schedule. _next_ping_due_us is the local timestamp at
    // which send_ping_() should next fire; _bursts_remaining counts
    // down the initial fast-fill series.
    int64_t _next_ping_due_us = 0;
    size_t  _bursts_remaining = 0;

    // Filter window. Bounded ring of WINDOW_N entries. _samples_count
    // is the live entry count (<= WINDOW_N). Samples are kept in
    // arrival order; the filter sorts copies it doesn't mutate the
    // window in place.
    SyncSample _samples[WINDOW_N];
    size_t     _samples_count = 0;

    // Last seq successfully sent. Next ping carries _last_sent_seq+1.
    // Lifetime-of-boot counter; not reset on link-down.
    uint32_t _last_sent_seq = 0;

    // Outstanding-round bookkeeping. Only one round is outstanding
    // at a time; a second ping doesn't fire until the previous round
    // completes or times out. While outstanding, the in-flight seq
    // equals _last_sent_seq; PONGs are matched on (_last_sent_seq, _round_t1_us).
    bool    _round_outstanding = false;
    int64_t _round_t1_us       = 0;
};

}  // namespace controller_link
