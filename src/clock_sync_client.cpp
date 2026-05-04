#include "clock_sync_client.h"

#include <algorithm>
#include <cstring>

#include "device_identity.h"
#include "platform_clock.h"
#include "synced_clock.h"
#include "udp_transport.h"
#include "wire_constants.h"

namespace {

// ---- Filter and timing tuning -------------------------------------------
//
// All knobs in one place. See drafts/synced_clock.md for the rationale.
// The short version: tuned for typical Wi-Fi LAN with 5-30 ms RTT in
// normal operation, occasional spikes, and the LED-sync visual
// tolerance (~10 ms perceptible).

constexpr size_t  WINDOW_N             = ClockSyncClient::WINDOW_N;
constexpr size_t  BEST_K_BY_RTT        = 3;
constexpr size_t  MIN_SAMPLES_TO_APPLY = BEST_K_BY_RTT;

// RTTs above this are almost certainly Wi-Fi retransmits or controller
// stalls, not signal. Carries over from the v2 controller filter.
constexpr int64_t RTT_GATE_US     = 200 * 1000;            // 200 ms

// Steady cadence: one ping per 15 s renews the LEASE_US (55 s) lease
// with comfortable headroom for one missed renewal.
constexpr int64_t STEADY_INTERVAL_US = 15LL * 1000 * 1000; // 15 s

// Burst cadence: 500 ms between pings while the filter window fills.
// Burst exits on the first applied lease or on the deadline below.
constexpr int64_t BURST_INTERVAL_US  = 500 * 1000;         // 500 ms
constexpr int64_t BURST_DURATION_US  = 10LL * 1000 * 1000; // 10 s

// Lease handed to SyncedClock with each apply. Carries from v2's
// SYNC_LEASE_MS (55 s).
constexpr int64_t LEASE_US           = 55LL * 1000 * 1000; // 55 s

// Sync UDP port. See drafts/controller_link.md appendix E.
constexpr uint16_t SYNC_PORT = 6043;

// Packet type bytes.
constexpr uint8_t PKT_PING = 0x01;
constexpr uint8_t PKT_PONG = 0x02;

// Wire layouts. See drafts/synced_clock.md appendix A.
//   PING : type | uid[16] | device_boot_token | seq | t1
//          1   + 16       + 4                 + 4   + 8 = 33 bytes
//   PONG : type | controller_boot_token | seq | t1 | t2 | t3
//          1   + 4                      + 4   + 8  + 8  + 8  = 33 bytes
constexpr size_t PING_WIRE_SIZE = 33;
constexpr size_t PONG_WIRE_SIZE = 33;

// PONG field offsets (post type byte).
constexpr size_t PONG_OFF_TOKEN = 1;
constexpr size_t PONG_OFF_SEQ   = 5;
constexpr size_t PONG_OFF_T1    = 9;
constexpr size_t PONG_OFF_T2    = 17;
constexpr size_t PONG_OFF_T3    = 25;

}  // namespace

// ------------------------------------------------------------------------

ClockSyncClient::ClockSyncClient(UdpTransport& udp, SyncedClock& clock,
                                 const DeviceIdentity& identity)
    : _udp(udp), _clock(clock), _identity(identity),
      _last_local_boot_token(identity.boot_token)
{
    // Socket bind is lazy in poll(); a bind during construction would
    // fight the network stack at boot when Wi-Fi may not yet be up.
}

void ClockSyncClient::set_controller(uint32_t ipv4_be)
{
    if (ipv4_be == _target_ip) {
        // Idempotent: the App calls this every tick from
        // link.controller_ip_addr(), which is stable while the link
        // stays in the same state.
        return;
    }

    _target_ip = ipv4_be;

    reset_filter_();
    _udp.close();
    // _last_controller_boot_token is intentionally NOT cleared here.
    // SyncedClock's lease rides across target changes; the token that
    // lease was measured against has to ride with it, otherwise a same
    // IP reconnect after a controller reboot would silently re-seed
    // and never detect the epoch change.

    if (ipv4_be == 0) {
        // Going idle. Drop schedule; SyncedClock keeps its lease so a
        // transient drop does not invalidate the math.
        _in_burst         = false;
        _next_ping_due_us = 0;
        return;
    }

    start_burst_();
}

void ClockSyncClient::poll()
{
    // Local-boot detection runs first so an in-process simulated reboot
    // while idle still clears the now-meaningless lease.
    if (_identity.boot_token != _last_local_boot_token) {
        on_local_boot_change_();
    }

    if (_target_ip == 0) {
        return;
    }

    // Lazy bind. A failure here just delays the first ping by a tick;
    // no special backoff path.
    if (!_udp.is_bound() && !_udp.bind(0)) {
        return;
    }

    drain_responses_();

    // drain_responses_ may have closed the socket on a recv error.
    if (!_udp.is_bound()) {
        return;
    }

    if (schedule_due_()) {
        // The schedule is also the round budget: any round still
        // outstanding when the next send time arrives is treated as
        // lost (no PONG within one cadence), and the new ping
        // replaces it. This is what gives burst-with-loss any chance
        // of progressing, and what lets a transient recv error
        // recover on the next tick.
        _round_outstanding = false;
        send_ping_();
    }
}

void ClockSyncClient::reset_filter_()
{
    _samples_count     = 0;
    _round_outstanding = false;
    _round_t1_us       = 0;
}

void ClockSyncClient::start_burst_()
{
    const int64_t now    = now_us_();
    _in_burst            = true;
    _burst_deadline_us   = now + BURST_DURATION_US;
    _next_ping_due_us    = now;  // fire immediately on the next poll()
}

void ClockSyncClient::on_local_boot_change_()
{
    // Local monotonic timeline reset. The existing lease anchored
    // _valid_until_local_us against the old timeline and is no longer
    // meaningful; the offset itself was also computed against samples
    // from the old timeline.
    _last_local_boot_token = _identity.boot_token;
    _clock.clear_sync();
    reset_filter_();
    if (_target_ip != 0) {
        start_burst_();
    }
}

void ClockSyncClient::on_remote_epoch_change_()
{
    // Controller rebooted on the same hardware: same IP, fresh
    // monotonic. Mixing pre- and post-reboot samples produces a
    // meaningless median, and the existing lease refers to the old
    // remote timeline.
    _clock.clear_sync();
    reset_filter_();
    start_burst_();
}

bool ClockSyncClient::schedule_due_()
{
    const int64_t now = now_us_();

    // Burst exit on deadline: caps the runaway-cadence window if no
    // PONG ever lands. The other exit path (first lease applied) lives
    // in apply_filter_().
    if (_in_burst && now >= _burst_deadline_us) {
        _in_burst = false;
    }

    return now >= _next_ping_due_us;
}

void ClockSyncClient::send_ping_()
{
    const uint32_t seq = _last_sent_seq + 1;
    const int64_t  t1  = now_us_();

    uint8_t pkt[PING_WIRE_SIZE] = {};
    pkt[0] = PKT_PING;

    // Copy up to UID_WIRE_SIZE bytes of the C-string uid into the
    // fixed-size wire slot. The buffer is zero-initialized, so any
    // bytes past strlen are already null padding.
    const size_t uid_len = std::min(std::strlen(_identity.uid),
                                    static_cast<size_t>(UID_WIRE_SIZE));
    std::memcpy(pkt + 1, _identity.uid, uid_len);

    std::memcpy(pkt + 1 + UID_WIRE_SIZE,     &_identity.boot_token, 4);
    std::memcpy(pkt + 1 + UID_WIRE_SIZE + 4, &seq,                  4);
    std::memcpy(pkt + 1 + UID_WIRE_SIZE + 8, &t1,                   8);

    if (!_udp.send(pkt, sizeof(pkt), _target_ip, SYNC_PORT)) {
        // Send failed: close so next tick rebinds. The schedule is
        // left where it was; the next poll() will see _next_ping_due_us
        // already past and try again. No state commit happened.
        _udp.close();
        return;
    }

    _last_sent_seq     = seq;
    _round_t1_us       = t1;
    _round_outstanding = true;

    const int64_t interval = _in_burst ? BURST_INTERVAL_US : STEADY_INTERVAL_US;
    _next_ping_due_us      = t1 + interval;
}

void ClockSyncClient::drain_responses_()
{
    // Buffer larger than PONG_WIRE_SIZE so an oversized datagram
    // returns its actual length (some larger value) and gets rejected
    // by the size check, instead of being truncated to exactly 33
    // bytes and slipping through.
    uint8_t  buf[64];
    uint32_t src_ip   = 0;
    uint16_t src_port = 0;

    while (true) {
        const int n = _udp.recv(buf, sizeof(buf), &src_ip, &src_port);
        if (n == 0) {
            return;  // nothing waiting
        }
        if (n < 0) {
            // Socket error: rebind next tick.
            _udp.close();
            return;
        }

        // Capture t4 as close to receipt as possible.
        const int64_t t4 = now_us_();

        if (src_ip != _target_ip)                  continue;
        if (n != static_cast<int>(PONG_WIRE_SIZE)) continue;
        if (buf[0] != PKT_PONG)                    continue;

        uint32_t controller_token = 0;
        uint32_t seq              = 0;
        int64_t  t1               = 0;
        int64_t  t2               = 0;
        int64_t  t3               = 0;
        std::memcpy(&controller_token, buf + PONG_OFF_TOKEN, 4);
        std::memcpy(&seq,              buf + PONG_OFF_SEQ,   4);
        std::memcpy(&t1,               buf + PONG_OFF_T1,    8);
        std::memcpy(&t2,               buf + PONG_OFF_T2,    8);
        std::memcpy(&t3,               buf + PONG_OFF_T3,    8);

        // Round match BEFORE token check. A stale or duplicate PONG
        // that doesn't match our outstanding round must not be able to
        // wipe SyncedClock just by carrying a different token; only a
        // PONG that round-matches our most recent PING is fresh enough
        // to be trusted as evidence of an epoch change.
        if (!_round_outstanding) continue;
        if (seq != _last_sent_seq) continue;
        if (t1 != _round_t1_us)    continue;

        // Round consumed regardless of what we do with the timestamps.
        _round_outstanding = false;

        // Token check on the matched round.
        //
        // _last_controller_boot_token == 0 is the unseeded sentinel;
        // the protocol reserves 0 (controllers regenerate a non-zero
        // token if rand happens to produce 0, mirroring how the device
        // side handles its own boot_token).
        if (_last_controller_boot_token != 0 &&
            _last_controller_boot_token != controller_token) {
            // Remote epoch changed between our send and this reply.
            // The PONG's t2/t3 are from the new epoch's monotonic
            // timeline, so we discard it as a sample (no
            // process_round_) and let the burst restart yield a clean
            // first measurement against the new epoch.
            _last_controller_boot_token = controller_token;
            on_remote_epoch_change_();
            continue;
        }

        // Seed on first matched round; subsequent matched rounds
        // reassign the same value (no-op).
        _last_controller_boot_token = controller_token;

        process_round_(t1, t2, t3, t4);
    }
}

void ClockSyncClient::process_round_(int64_t t1, int64_t t2, int64_t t3, int64_t t4)
{
    // NTP four-timestamp formula. SyncedClock stores offset as
    // local minus remote; rearrange to that sign so we do not negate
    // at apply time.
    const int64_t rtt    = (t4 - t1) - (t3 - t2);
    const int64_t offset = ((t1 - t2) + (t4 - t3)) / 2;

    if (rtt < 0 || rtt > RTT_GATE_US) return;

    if (_samples_count < WINDOW_N) {
        _samples[_samples_count] = SyncSample{offset, rtt};
        _samples_count += 1;
    } else {
        // Drop oldest, append at end.
        for (size_t i = 1; i < WINDOW_N; ++i) {
            _samples[i - 1] = _samples[i];
        }
        _samples[WINDOW_N - 1] = SyncSample{offset, rtt};
    }

    if (_samples_count >= MIN_SAMPLES_TO_APPLY) {
        apply_filter_();
    }
}

void ClockSyncClient::apply_filter_()
{
    SyncSample sorted[WINDOW_N];
    for (size_t i = 0; i < _samples_count; ++i) {
        sorted[i] = _samples[i];
    }
    std::sort(sorted, sorted + _samples_count,
              [](const SyncSample& a, const SyncSample& b) {
                  return a.rtt_us < b.rtt_us;
              });

    const size_t k = std::min(_samples_count, BEST_K_BY_RTT);

    int64_t offsets[BEST_K_BY_RTT];
    for (size_t i = 0; i < k; ++i) {
        offsets[i] = sorted[i].offset_us;
    }
    std::sort(offsets, offsets + k);
    const int64_t median_offset = offsets[k / 2];

    _clock.apply_sync_offset(median_offset, LEASE_US);

    // First applied lease exits the burst. Reschedule the next ping on
    // the steady cadence; otherwise the previous send's burst-cadence
    // _next_ping_due_us would produce one stale near-immediate send
    // before steady kicks in.
    if (_in_burst) {
        _in_burst         = false;
        _next_ping_due_us = now_us_() + STEADY_INTERVAL_US;
    }
}

int64_t ClockSyncClient::now_us_() const
{
    return platform_clock::now_us();
}
