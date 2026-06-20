#pragma once

#include <cstddef>
#include <cstdint>

class SyncedClock;
class UdpTransport;
struct DeviceIdentity;

// ClockSyncClient is the device's clock-sync agent. It runs
// NTP-style ping/pong rounds against the controller's sync server
// and keeps a SyncedClock instance updated with the resulting
// offset estimates.

// One accepted sync round. The filter ranks by rtt_us ascending.
struct SyncSample {
    int64_t offset_us;  // local minus remote
    int64_t rtt_us;     // round-trip
};

class ClockSyncClient {
public:
    ClockSyncClient(UdpTransport& udp, SyncedClock& clock,
                    const DeviceIdentity& identity);

    // Set the sync target (IPv4, big-endian; 0 = idle). Changing
    // target resets state and restarts the burst; same target is a
    // no-op (App may call every tick unconditionally).
    void set_controller(uint32_t ip_addr);

    // Per-tick entry: schedule, send, drain replies.
    void poll();

    // Filter window size.
    static constexpr size_t WINDOW_N = 5;

private:
    // Clear sample window and any outstanding round.
    void reset_filter_();

    // Enter burst: fast cadence until the first applied lease or
    // BURST_DURATION_US, whichever first.
    void start_burst_();

    // Handle a controller reboot (PONG boot token changed): clear
    // the lease, reset the filter, restart the burst.
    void on_remote_epoch_change_();

    // Is the next ping due now? Side effect: clears the burst flag
    // once its deadline elapses.
    bool schedule_due_();

    // Build and send a PING. Round/seq/schedule commit only on a
    // successful send; a failed send closes the socket and backs off.
    void send_ping_();

    // Drain available PONGs; validate, detect epoch change, match
    // (seq, t1) against the outstanding round, hand off to
    // process_round_().
    void drain_responses_();

    // NTP four-timestamp formula, RTT gate, push into window,
    // apply_filter_() if enough samples.
    void process_round_(int64_t t1, int64_t t2, int64_t t3, int64_t t4);

    // Best-K-by-RTT, median of those K offsets, write to SyncedClock
    // with a fresh lease, exit burst on first apply.
    void apply_filter_();

    // Local monotonic time.
    int64_t now_us_() const;

    UdpTransport&         _udp;
    SyncedClock&          _clock;
    const DeviceIdentity& _identity;

    // Controller IPv4 (big-endian); 0 = idle.
    uint32_t _target_ip = 0;

    // Controller boot token from the last accepted PONG; 0 means
    // not yet seen.
    uint32_t _last_controller_boot_token = 0;

    // Recent accepted samples, kept in arrival order.
    SyncSample _samples[WINDOW_N] = {};
    size_t     _samples_count     = 0;

    // Ping schedule. Interval is BURST_INTERVAL_US in burst mode,
    // STEADY_INTERVAL_US otherwise.
    int64_t _next_ping_due_us  = 0;
    int64_t _burst_deadline_us = 0;
    bool    _in_burst          = false;

    // Lifetime counter; next ping carries _last_sent_seq + 1.
    uint32_t _last_sent_seq = 0;

    // Ping in flight. Only one flies at a time; its seq equals
    // _last_sent_seq.
    bool    _ping_in_flight = false;
    int64_t _ping_t1_us     = 0;
};
