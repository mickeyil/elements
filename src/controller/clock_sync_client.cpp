#include "controller/clock_sync_client.h"

#include <algorithm>
#include <cstring>

#include "platform/device_identity.h"
#include "platform/platform_clock.h"
#include "controller/synced_clock.h"
#include "platform/udp_transport.h"

namespace {

// Filter and timing tuning, all knobs in one place. Tuned for typical
// Wi-Fi LAN (5-30 ms RTT, occasional spikes) against the LED-sync
// ~10 ms perceptible threshold.

constexpr size_t  WINDOW_N             = ClockSyncClient::WINDOW_N;
constexpr size_t  BEST_K_BY_RTT        = 3;
constexpr size_t  MIN_SAMPLES_TO_APPLY = BEST_K_BY_RTT;

// RTTs above this are almost certainly Wi-Fi retransmits or
// controller stalls, not signal.
constexpr int64_t RTT_GATE_MS        = 200;

// Steady interval: one ping per 15 s renews the 55 s lease with
// comfortable headroom for one missed renewal.
constexpr int64_t STEADY_INTERVAL_SEC = 15;

// Burst interval: 500 ms between pings while the filter window
// fills. Burst exits on the first applied lease or on the
// deadline below.
constexpr int64_t BURST_INTERVAL_MS  = 500;
constexpr int64_t BURST_DURATION_SEC = 10;

// Lease handed to SyncedClock with each apply.
constexpr int64_t LEASE_SEC          = 55;

// Sync UDP port; the controller config holds the same value.
constexpr uint16_t SYNC_PORT = 6043;

// Packet type bytes.
constexpr uint8_t PKT_PING = 0x01;
constexpr uint8_t PKT_PONG = 0x02;

constexpr size_t PING_WIRE_SIZE = 33;
constexpr size_t PONG_WIRE_SIZE = 33;

// PONG field offsets (after the type byte).
constexpr size_t PONG_OFF_TOKEN = 1;
constexpr size_t PONG_OFF_SEQ   = 5;
constexpr size_t PONG_OFF_T1    = 9;
constexpr size_t PONG_OFF_T2    = 17;
constexpr size_t PONG_OFF_T3    = 25;

}  // namespace

ClockSyncClient::ClockSyncClient(UdpTransport& udp, SyncedClock& clock,
                                 const DeviceIdentity& identity)
    : _udp(udp), _clock(clock), _identity(identity)
{
    // Bind is lazy: at boot the Wi-Fi stack may not be up yet.
}

void ClockSyncClient::set_controller(uint32_t ip_addr)
{
    if (ip_addr == _target_ip) {
        return;
    }

    _target_ip = ip_addr;

    reset_filter_();
    _udp.close();
    // Keep _last_controller_boot_token: needed to detect a reboot
    // during disconnect.

    if (ip_addr == 0) {
        // No controller: stop sending; keep the existing lease.
        _in_burst         = false;
        _next_ping_due_us = 0;
        return;
    }

    start_burst_();
}

void ClockSyncClient::poll()
{
    if (_target_ip == 0) {
        return;
    }

    // Lazy bind. A failure just delays the first ping; retry
    // next tick.
    if (!_udp.is_bound() && !_udp.bind(0)) {
        return;
    }

    drain_responses_();

    // drain_responses_ may have closed the socket on a recv error.
    if (!_udp.is_bound()) {
        return;
    }

    if (schedule_due_()) {
        // If a previous ping is still in flight when the next send
        // time arrives, treat it as lost (no PONG within one
        // interval) and replace it. Lets bursts make progress
        // under packet loss.
        _ping_in_flight = false;
        send_ping_();
    }
}

void ClockSyncClient::reset_filter_()
{
    _samples_count  = 0;
    _ping_in_flight = false;
    _ping_t1_us     = 0;
}

void ClockSyncClient::start_burst_()
{
    const int64_t now    = now_us_();
    _in_burst            = true;
    _burst_deadline_us   = now + BURST_DURATION_SEC * 1'000'000;
    _next_ping_due_us    = now;  // fire next poll()
}

void ClockSyncClient::on_remote_epoch_change_()
{
    // Controller rebooted: fresh monotonic timeline. Mixing pre-
    // and post-reboot samples gives a meaningless median, and the
    // existing lease points at the old timeline.
    _clock.clear_sync();
    reset_filter_();
    start_burst_();
}

bool ClockSyncClient::schedule_due_()
{
    const int64_t now = now_us_();

    // Burst exit on deadline: caps the burst window if no PONG
    // ever lands. The other exit path (first applied lease) is
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

    // Copy up to UID_SIZE bytes from uid into the wire slot. The
    // packet was zero-initialized, so any bytes past the UID's
    // length are already null padding.
    const size_t uid_len = std::min(std::strlen(_identity.uid),
                                    static_cast<size_t>(UID_SIZE));
    std::memcpy(pkt + 1, _identity.uid, uid_len);

    std::memcpy(pkt + 1 + UID_SIZE,     &_identity.boot_token, 4);
    std::memcpy(pkt + 1 + UID_SIZE + 4, &seq,                  4);
    std::memcpy(pkt + 1 + UID_SIZE + 8, &t1,                   8);

    if (!_udp.send(pkt, sizeof(pkt), _target_ip, SYNC_PORT)) {
        // Send failed: close so next tick rebinds. The schedule is
        // unchanged, so the next poll() will retry immediately.
        _udp.close();
        return;
    }

    _last_sent_seq    = seq;
    _ping_t1_us       = t1;
    _ping_in_flight   = true;

    const int64_t interval = _in_burst ? BURST_INTERVAL_MS * 1000
                                       : STEADY_INTERVAL_SEC * 1'000'000;
    _next_ping_due_us = t1 + interval;
}

void ClockSyncClient::drain_responses_()
{
    // One byte larger than PONG_WIRE_SIZE so the size check below
    // can reject oversized datagrams.
    uint8_t  buf[PONG_WIRE_SIZE + 1];
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

        // Match the in-flight ping before checking the token,
        // so a stale PONG carrying a different token can't
        // wipe SyncedClock.
        if (!_ping_in_flight) continue;
        if (seq != _last_sent_seq) continue;
        if (t1 != _ping_t1_us)    continue;

        // Round consumed regardless of what we do with the timestamps.
        _ping_in_flight = false;

        // Token check on the matched round. Skip until we've seen
        // a token at least once (0 is the unseeded sentinel).
        if (_last_controller_boot_token != 0 &&
            _last_controller_boot_token != controller_token) {
            // Controller rebooted between our send and this reply.
            // The PONG's t2/t3 belong to the new timeline, so we
            // drop this sample; the restarted burst will produce
            // a clean first measurement.
            _last_controller_boot_token = controller_token;
            on_remote_epoch_change_();
            continue;
        }

        // Seed on first matched round; same-token rounds re-assign
        // the same value.
        _last_controller_boot_token = controller_token;

        process_round_(t1, t2, t3, t4);
    }
}

void ClockSyncClient::process_round_(int64_t t1, int64_t t2, int64_t t3, int64_t t4)
{
    // NTP four-timestamp formula. Sign convention: local minus
    // remote, matching SyncedClock's storage so apply doesn't
    // need to negate.
    const int64_t rtt    = (t4 - t1) - (t3 - t2);
    const int64_t offset = ((t1 - t2) + (t4 - t3)) / 2;

    if (rtt < 0 || rtt > RTT_GATE_MS * 1000) return;

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

    _clock.apply_sync_offset(median_offset, LEASE_SEC * 1'000'000);

    // First applied lease exits the burst. Reschedule on the
    // steady interval, otherwise the previous send's burst-interval
    // due time would produce one stale near-immediate send before
    // steady kicks in.
    if (_in_burst) {
        _in_burst         = false;
        _next_ping_due_us = now_us_() + STEADY_INTERVAL_SEC * 1'000'000;
    }
}

int64_t ClockSyncClient::now_us_() const
{
    return now_us();
}
