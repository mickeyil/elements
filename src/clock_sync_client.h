#pragma once

#include <cstddef>
#include <cstdint>

class SyncedClock;
class UdpTransport;
struct DeviceIdentity;

// Device-side feeder for SyncedClock. Owns the UDP socket on the sync
// port, the ping schedule, the filter that turns NTP four-timestamp
// rounds into an offset estimate, and the contracts that decide when
// to invalidate that estimate.
//
// Scope is exactly clock estimation against a configured peer. Authority
// (who is allowed to be the controller, what TCP commands have been
// accepted) lives in ControllerLink. The owner bridges the two:
//
//     network.poll();
//     link.poll();
//     sync.set_controller(link.controller_ip_addr());  // 0 when !ready
//     sync.poll();
//
// poll() is the single per-tick entry point.
//
// Reset triggers, all self-contained:
//   - target IP changes (set_controller)            : reset filter and
//     drop outstanding round; clear remembered controller boot token;
//     restart burst (or go idle on 0). SyncedClock is left alone so
//     its lease can ride to expiry on a transient drop.
//   - controller boot token in PONG changes         : remote clock
//     epoch jumped (controller rebooted on the same hardware). Clear
//     SyncedClock, reset filter, drop the round, discard the
//     triggering PONG, restart burst. Next ping fires immediately.
//   - local DeviceIdentity.boot_token changes       : in-process sim
//     reboot. Local monotonic timeline reset; the existing lease
//     refers to the old timeline. Clear SyncedClock, reset filter,
//     drop the round; if a target is set, restart burst. Checked
//     before the no-target early-return so a reboot-while-idle still
//     invalidates the old lease.
//
// Burst: 500 ms cadence, exits on the first applied lease or on a
// 10 s deadline (whichever comes first). Steady cadence is 15 s.
//
// On the wire (see drafts/synced_clock.md Appendix A):
//   PING 33 bytes : type | uid[16] | device_boot_token | seq | t1
//   PONG 33 bytes : type | controller_boot_token | seq | t1 | t2 | t3
//
// PONG validation:
//   - src_ip matches the configured target (else discarded)
//   - controller_boot_token: seeds on first match, triggers epoch reset
//     on change
//   - seq matches the outstanding _last_sent_seq
//   - t1 matches the outstanding _round_t1_us
//   - RTT in [0, RTT_GATE_US]

// One accepted sync round. The filter ranks by rtt_us ascending.
struct SyncSample {
    int64_t offset_us;  // local minus remote, in us
    int64_t rtt_us;     // network round-trip, in us
};

class ClockSyncClient {
public:
    ClockSyncClient(UdpTransport& udp, SyncedClock& clock,
                    const DeviceIdentity& identity);

    // Configure the sync target. 0 means "no peer; go idle." A non-zero
    // value different from the current target resets internal state and
    // restarts the burst. Setting the same non-zero target again is a
    // no-op so the App can call this every tick unconditionally.
    void set_controller(uint32_t ipv4_be);

    // Single per-tick entry point. Drives the local-boot-token check,
    // the schedule, sends, and reply processing.
    void poll();

    // Filter window size. Tests reach for this when constructing
    // sample sequences.
    static constexpr size_t WINDOW_N = 5;

private:
    // Reset filter window and outstanding round. Does not touch
    // SyncedClock or schedule; callers compose the rest.
    void reset_filter_();

    // Begin or restart the burst window: fast cadence until first
    // lease applied or BURST_DURATION_US elapses, whichever first.
    void start_burst_();

    // Detected via _identity.boot_token changing under us. Local
    // timeline reset; the existing lease is meaningless.
    void on_local_boot_change_();

    // Detected via PONG.controller_boot_token differing from the seeded
    // value. Remote timeline reset.
    void on_remote_epoch_change_();

    // True iff the next ping is due now. Updates the burst-mode flag
    // when the burst deadline passes.
    bool schedule_due_();

    // Build and send a PING. State commits (round outstanding, seq,
    // t1, next-ping schedule) happen only after the send succeeds;
    // a failed send closes the socket and backs off briefly.
    void send_ping_();

    // Drain available PONGs. Validate src_ip, size, type. Detect
    // remote epoch change (and discard the triggering PONG). Match
    // (seq, t1) against the outstanding round. Call process_round_()
    // on accepted rounds.
    void drain_responses_();

    // NTP four-timestamp formula, RTT gate, push into window,
    // apply_filter_() if enough samples.
    void process_round_(int64_t t1, int64_t t2, int64_t t3, int64_t t4);

    // Best-K-by-RTT, median of those K offsets, write to SyncedClock
    // with a fresh lease, exit burst on first apply.
    void apply_filter_();

    // Local monotonic clock in microseconds. Indirection point for
    // tests; production reads now_us() directly.
    int64_t now_us_() const;

    UdpTransport&         _udp;
    SyncedClock&          _clock;
    const DeviceIdentity& _identity;

    // 0 means idle. Non-zero is the controller's IPv4 in network byte
    // order (matches UdpTransport's send/recv addressing convention).
    uint32_t _target_ip = 0;

    // Last-seen device boot_token. Compared against _identity.boot_token
    // each tick to detect in-process simulated reboot. Seeded from the
    // current identity in the ctor so first poll() does not spuriously
    // fire on_local_boot_change_().
    uint32_t _last_local_boot_token = 0;

    // Last-seen controller boot token, from PONG. 0 is the unseeded
    // sentinel; the protocol reserves 0 (controllers regenerate
    // non-zero tokens). First matching round seeds it; subsequent
    // matching rounds either reassign the same value (no-op) or
    // trigger an epoch change.
    //
    // Co-managed with SyncedClock's lease: rides across target
    // changes so a same-IP reconnect after a controller reboot still
    // detects the epoch change against the lease's original token,
    // instead of silently re-seeding from the new controller.
    uint32_t _last_controller_boot_token = 0;

    // Filter window. Bounded ring of WINDOW_N entries. _samples_count
    // is the live entry count (<= WINDOW_N). Samples kept in arrival
    // order; the filter sorts a copy and does not mutate the window.
    SyncSample _samples[WINDOW_N] = {};
    size_t     _samples_count     = 0;

    // Ping schedule. _next_ping_due_us is the local timestamp at which
    // send_ping_() should next fire. While _in_burst is true, cadence
    // is BURST_INTERVAL_US; otherwise STEADY_INTERVAL_US.
    int64_t _next_ping_due_us = 0;
    int64_t _burst_deadline_us = 0;
    bool    _in_burst          = false;

    // Last seq successfully sent. Next ping carries _last_sent_seq + 1.
    // Lifetime-of-process counter; never reset (any 32-bit collision is
    // both extraordinarily improbable and harmless given t1 also has
    // to match).
    uint32_t _last_sent_seq = 0;

    // Outstanding-round bookkeeping. Only one round is outstanding at a
    // time; a second ping does not fire until either the previous round
    // resolves or the schedule allows a fresh send. While outstanding,
    // the in-flight seq equals _last_sent_seq.
    bool    _round_outstanding = false;
    int64_t _round_t1_us       = 0;
};
