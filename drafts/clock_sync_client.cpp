// SKELETON -- not expected to compile. Represents the algorithm and
// state transitions that fall out of the decisions in
// drafts/synced_clock.md. Real impl will fill in headers, error
// handling, and any platform glue.

#include "clock_sync_client.h"

#include <algorithm>
#include <cstring>

#include "controller_link.h"
#include "udp_transport.h"
#include "synced_clock.h"
#include "platform_clock.h"
#include "wire_constants.h"

namespace controller_link {

namespace {

// ---- Filter and timing tuning -------------------------------------------
//
// All knobs in one place. See drafts/synced_clock.md for the
// rationale; the short version: tuned for typical Wi-Fi LAN with
// 5-30 ms RTT during normal operation, occasional spikes, and the
// LED-sync visual tolerance (~10 ms perceptible).

// Filter window: how many recent samples are kept for the
// best-K-by-RTT selection.
constexpr size_t WINDOW_N = 5;

// Of the WINDOW_N kept samples, sort by RTT and take the median
// offset of the K with lowest RTT. Lower-RTT samples are trusted
// more (less room for one-way network delay to be unequal between
// the two directions, which is what the offset formula assumes).
constexpr size_t BEST_K_BY_RTT = 3;

// Don't write to SyncedClock until this many samples have made it
// past the RTT gate. Without this hold-off, the first ping after
// link-up would issue a lease based on one possibly-jittery sample.
constexpr size_t MIN_SAMPLES_TO_APPLY = BEST_K_BY_RTT;

// Sanity gate. RTTs above this are almost certainly Wi-Fi
// retransmits or controller stalls, not signal. Carries over from
// the v2 controller filter.
constexpr int64_t RTT_GATE_US = 200 * 1000;  // 200 ms

// Normal operation: send a PING every 15 s. Renews the lease
// (LEASE_US below) comfortably within its window even if one
// renewal is missed.
constexpr int64_t STEADY_INTERVAL_US = 15 * 1000 * 1000;  // 15 s

// Right after the link becomes ready, send a short series of pings
// 500 ms apart so the filter window fills quickly and the first
// real lease can be issued without waiting for steady interval.
constexpr int64_t BURST_INTERVAL_US = 500 * 1000;          // 500 ms
constexpr size_t  BURST_PING_COUNT  = WINDOW_N;

// PONG must arrive within this window or the round is abandoned and
// a fresh one fires on the next scheduled ping. Long enough to
// swallow a Wi-Fi retransmit; short enough that we don't sit on a
// dead round forever.
constexpr int64_t ROUND_TIMEOUT_US = 1 * 1000 * 1000;      // 1 s

// Lease handed to SyncedClock with each apply. Matches v2's
// SYNC_LEASE_MS (55 s); chosen so STEADY_INTERVAL (15 s) covers it
// even with one missed renewal.
constexpr int64_t LEASE_US = 55 * 1000 * 1000;             // 55 s

// Sync UDP port. See drafts/controller_link.md appendix E.
constexpr uint16_t SYNC_PORT = 6043;

// Packet type bytes. Two-message exchange under device-computes;
// REPORT/LEASE types added later only if controller-computes wins.
constexpr uint8_t PKT_PING = 0x01;
constexpr uint8_t PKT_PONG = 0x02;

// Wire layouts. See drafts/synced_clock.md appendix A.
constexpr size_t PING_WIRE_SIZE = 33;  // type + uid[16] + boot_token + seq + t1
constexpr size_t PONG_WIRE_SIZE = 29;  // type + seq + t1 + t2 + t3

// ---- Filter helper ------------------------------------------------------

// Median of a small array. Sorts in place; for odd sizes returns
// the middle element, for even sizes the lower of the two middles.
// Integer microseconds; we don't need sub-us precision out of the
// median.
int64_t median_in_place(int64_t* values, size_t count) {
    std::sort(values, values + count);
    return values[count / 2];
}

}  // namespace

// ------------------------------------------------------------------------

ClockSyncClient::ClockSyncClient(
    UdpTransport&         udp,
    const ControllerLink& link,
    SyncedClock&          clock,
    const DeviceIdentity& identity)
    : _udp(udp), _link(link), _clock(clock), _identity(identity)
{
    // Bind happens lazily inside poll(). A constructor-time bind
    // can fail because the network isn't up yet during boot, and
    // there's no good recovery path from here.
}

void ClockSyncClient::poll()
{
    const bool ready_now = _link.is_ready();

    // The link just dropped: wipe filter state and clear the clock.
    if (_was_ready && !ready_now) {
        on_link_down_();
    }
    _was_ready = ready_now;

    if (!ready_now) {
        return;
    }

    // Lazy bind / re-bind. A bind failure here just delays the
    // first ping to the next tick; the network may come up shortly.
    //
    // TODO: add backoff if bind keeps failing. The skeleton retries
    // every tick, which is fine for correctness but on ESP a
    // persistent failure could spam logs / waste loop budget. Impl
    // can layer a simple "next bind attempt at +N ms" in here.
    if (!_bound) {
        _bound = _udp.bind(0);
        if (!_bound) {
            return;
        }
    }

    // 1. Drain any PONGs already in the socket buffer.
    drain_responses_();

    // 2. Time out the outstanding round if its PONG never arrived.
    if (_round_outstanding && (now_us_() - _round_t1_us) > ROUND_TIMEOUT_US) {
        _round_outstanding = false;
    }

    // 3. Send the next PING if the schedule is due and we're idle.
    if (_bound && !_round_outstanding && schedule_due_()) {
        send_ping_();
    }
}

void ClockSyncClient::on_link_down_()
{
    _samples_count     = 0;
    _round_outstanding = false;
    _seq               = 0;
    _next_ping_due_us  = 0;
    _bursts_remaining  = 0;

    // Release the socket. Next time the link comes back up, poll()
    // will re-bind.
    mark_unbound_();

    _clock.clear_sync();
}

void ClockSyncClient::mark_unbound_()
{
    if (_bound) {
        _udp.close();
        _bound = false;
    }
}

bool ClockSyncClient::schedule_due_()
{
    const int64_t now = now_us_();

    // First call after the link became ready: prime the schedule
    // and the burst counter so the first ping fires immediately.
    //
    // Burst semantics: BURST_PING_COUNT is the total number of
    // burst-spaced pings we want (5). _bursts_remaining counts
    // burst pings still to send AFTER the current one. So the
    // initial value is BURST_PING_COUNT - 1: send 1 now, then
    // remaining 4 more at burst spacing, then transition to the
    // normal interval. This keeps the total at exactly
    // BURST_PING_COUNT.
    if (_next_ping_due_us == 0) {
        _next_ping_due_us = now;
        _bursts_remaining = BURST_PING_COUNT - 1;
    }

    return now >= _next_ping_due_us;
}

void ClockSyncClient::send_ping_()
{
    const uint32_t controller_ip = _link.controller_ip_addr();
    if (controller_ip == 0) {
        // Defensive: link.is_ready() said true but ip is zero.
        // Skip this tick; will retry next.
        return;
    }

    // Build the PING using locals; only commit to client state if
    // the send succeeds. Failure should not consume a sequence
    // number or mark a round outstanding.
    const uint32_t seq = _seq + 1;
    const int64_t  t1  = now_us_();

    uint8_t pkt[PING_WIRE_SIZE];
    pkt[0] = PKT_PING;
    std::memcpy(pkt + 1,  _identity.uid,         UID_SIZE);
    std::memcpy(pkt + 17, &_identity.boot_token, 4);
    std::memcpy(pkt + 21, &seq,                  4);
    std::memcpy(pkt + 25, &t1,                   8);

    if (!_udp.send(pkt, sizeof(pkt), controller_ip, SYNC_PORT)) {
        // Transport failed (socket error, network gone, etc.).
        // Close the socket and mark unbound so poll() re-binds
        // cleanly next tick; back the schedule off briefly so we
        // don't spin.
        mark_unbound_();
        _next_ping_due_us = t1 + BURST_INTERVAL_US;
        return;
    }

    // Send succeeded -- commit state.
    _seq               = seq;
    _round_seq         = seq;
    _round_t1_us       = t1;
    _round_outstanding = true;

    // Schedule the next ping. Burst spacing while the window fills,
    // then the normal interval.
    if (_bursts_remaining > 0) {
        _bursts_remaining -= 1;
        _next_ping_due_us = t1 + BURST_INTERVAL_US;
    } else {
        _next_ping_due_us = t1 + STEADY_INTERVAL_US;

        // TODO: schedule jitter. With more than ~20 devices on the
        // same LAN, add a small random offset (e.g., +/-2 s) here so
        // the fleet doesn't synchronize its ping times and produce
        // periodic contention spikes on the controller's Wi-Fi.
        // See drafts/synced_clock.md "Schedule jitter at scale."
    }
}

void ClockSyncClient::drain_responses_()
{
    uint8_t  buf[64];
    uint32_t src_ip   = 0;
    uint16_t src_port = 0;

    while (true) {
        const int n = _udp.recv(buf, sizeof(buf), &src_ip, &src_port);
        if (n == 0) {
            break;  // no data right now
        }
        if (n < 0) {
            // Socket error. Close + mark unbound so the next tick
            // re-binds cleanly. Drop any outstanding round; whatever
            // was in flight is lost with this socket.
            mark_unbound_();
            _round_outstanding = false;
            break;
        }
        if (n != static_cast<int>(PONG_WIRE_SIZE) || buf[0] != PKT_PONG) {
            continue;  // junk; ignore
        }

        // Identity validation by source IP. PONG carries no UID or
        // boot_token in its payload (see drafts/synced_clock.md
        // "Wire flow"); the source address has to do the work.
        // Source port is not checked -- the controller may reply
        // from an ephemeral port depending on its socket impl.
        if (src_ip != _link.controller_ip_addr()) {
            continue;
        }

        // Parse: type[1] + seq[4] + t1[8] + t2[8] + t3[8]
        uint32_t seq = 0;
        int64_t  t1  = 0;
        int64_t  t2  = 0;
        int64_t  t3  = 0;
        std::memcpy(&seq, buf + 1,  4);
        std::memcpy(&t1,  buf + 5,  8);
        std::memcpy(&t2,  buf + 13, 8);
        std::memcpy(&t3,  buf + 21, 8);

        // Match against the outstanding round. A PONG that doesn't
        // match (late retransmit, stale boot, replay) is dropped.
        // (seq, t1) is the unique round identifier on the device
        // side; controller-side validation of boot_token in the
        // PING discards stale attachments before they ever produce
        // a PONG.
        if (!_round_outstanding || seq != _round_seq || t1 != _round_t1_us) {
            continue;
        }

        const int64_t t4 = now_us_();
        _round_outstanding = false;

        process_round_(t1, t2, t3, t4);
    }
}

void ClockSyncClient::process_round_(int64_t t1, int64_t t2, int64_t t3, int64_t t4)
{
    // NTP four-timestamp formula. The offset estimate is exact only
    // if one-way delay is the same outbound and inbound; total RTT
    // bounds how far off that assumption can be. See
    // drafts/synced_clock.md "What RTT means" for the derivation.
    const int64_t rtt = (t4 - t1) - (t3 - t2);
    const int64_t remote_minus_local = ((t2 - t1) + (t3 - t4)) / 2;

    // Sanity gate: drop nonsense or grossly inflated samples before
    // they enter the window.
    if (rtt <= 0 || rtt > RTT_GATE_US) {
        return;
    }

    // Bounded ring: drop oldest when full, then append.
    if (_samples_count >= WINDOW_N) {
        for (size_t i = 1; i < WINDOW_N; ++i) {
            _samples[i - 1] = _samples[i];
        }
        _samples_count = WINDOW_N - 1;
    }
    _samples[_samples_count].remote_minus_local_us = remote_minus_local;
    _samples[_samples_count].rtt_us                = rtt;
    _samples_count += 1;

    // Hold off the first apply until the window has enough
    // candidates for a real best-K selection.
    if (_samples_count < MIN_SAMPLES_TO_APPLY) {
        return;
    }

    apply_filter_();
}

void ClockSyncClient::apply_filter_()
{
    // Sort the window by RTT ascending, take the K samples with the
    // lowest RTT, then take the median of their offsets.
    //
    // Why this and not plain median: lower RTT bounds the asymmetry
    // budget more tightly, so those samples' offset estimates are
    // more trustworthy. NTP itself uses K=1 (the single
    // minimum-delay sample); we use K=3 so a single unlucky-but-low
    // sample doesn't dominate.

    Sample sorted[WINDOW_N];
    for (size_t i = 0; i < _samples_count; ++i) {
        sorted[i] = _samples[i];
    }
    std::sort(sorted, sorted + _samples_count,
              [](const Sample& a, const Sample& b) {
                  return a.rtt_us < b.rtt_us;
              });

    const size_t k = (BEST_K_BY_RTT < _samples_count) ? BEST_K_BY_RTT : _samples_count;

    int64_t offsets[BEST_K_BY_RTT];
    for (size_t i = 0; i < k; ++i) {
        offsets[i] = sorted[i].remote_minus_local_us;
    }

    const int64_t median_remote_minus_local = median_in_place(offsets, k);

    // SyncedClock stores offset = local - remote. Negate before
    // writing.
    const int64_t synced_clock_offset_us = -median_remote_minus_local;

    _clock.apply_sync_offset(synced_clock_offset_us, LEASE_US);
}

int64_t ClockSyncClient::now_us_() const
{
    return platform_clock::now_us();
}

}  // namespace controller_link
