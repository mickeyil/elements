#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <cstring>
#include <deque>
#include <vector>

#include "../src/clock_sync_client.h"
#include "../src/device_identity.h"
#include "../src/synced_clock.h"
#include "../src/udp_transport.h"
#include "platform_clock_test.h"

// What is and is not tested here.
//
// In scope (testable without a controller):
//   - target set / clear / change behavior (UDP socket, filter, lease)
//   - first PING carries the identity boot_token
//   - first PONG seeds controller_boot_token; subsequent change triggers
//     a clean reset and discards the triggering PONG
//   - PONG validation gates (src_ip, size, type, seq, t1)
//   - filter applies a lease once enough samples land
//   - burst exits on first lease, falls back on deadline
//   - send failure closes the socket
//
// Out of scope (deferred until the focused Python sync server lands;
// requires a real UDP peer to round-trip against):
//   - end-to-end offset accuracy under realistic jitter / loss
//   - cross-controller-restart timing under real Wi-Fi

namespace {

constexpr uint32_t CONTROLLER_IP_A     = 0x0100A8C0;  // 192.168.0.1, BE
constexpr uint32_t CONTROLLER_IP_B     = 0x0200A8C0;  // 192.168.0.2, BE
constexpr uint32_t WRONG_IP            = 0x6402A8C0;  // 192.168.2.100, BE
constexpr uint16_t SYNC_PORT           = 6043;
constexpr int64_t  LEASE_US            = 55LL * 1000 * 1000;
constexpr int64_t  BURST_INTERVAL_US   = 500 * 1000;
constexpr int64_t  STEADY_INTERVAL_US  = 15LL * 1000 * 1000;
constexpr int64_t  BURST_DURATION_US   = 10LL * 1000 * 1000;

// ---- Fake UDP transport ----------------------------------------------------

class FakeUdpTransport : public UdpTransport {
public:
    struct SentPacket {
        std::vector<uint8_t> bytes;
        uint32_t             dst_ip   = 0;
        uint16_t             dst_port = 0;
    };
    struct IncomingPacket {
        std::vector<uint8_t> bytes;
        uint32_t             src_ip   = 0;
        uint16_t             src_port = 0;
    };

    bool bind(uint16_t local_port) override {
        if (force_bind_fail) return false;
        if (_bound) return local_port == 0 || local_port == _bound_port;
        _bound      = true;
        _bound_port = local_port == 0 ? 49152 : local_port;
        bind_count += 1;
        return true;
    }

    void close() override {
        if (_bound) close_count += 1;
        _bound      = false;
        _bound_port = 0;
    }

    bool is_bound() const override { return _bound; }

    bool send(const uint8_t* src, size_t len,
              uint32_t dst_ip, uint16_t dst_port) override
    {
        if (!_bound) return false;
        if (force_send_fail) return false;
        SentPacket p;
        p.bytes.assign(src, src + len);
        p.dst_ip   = dst_ip;
        p.dst_port = dst_port;
        sent.push_back(std::move(p));
        return true;
    }

    int recv(uint8_t* dst, size_t n,
             uint32_t* src_ip, uint16_t* src_port) override
    {
        if (!_bound) return -1;
        if (force_recv_error) {
            force_recv_error = false;
            return -1;
        }
        if (incoming.empty()) return 0;
        IncomingPacket pkt = std::move(incoming.front());
        incoming.pop_front();
        const size_t copy_len = std::min(n, pkt.bytes.size());
        std::memcpy(dst, pkt.bytes.data(), copy_len);
        *src_ip   = pkt.src_ip;
        *src_port = pkt.src_port;
        return static_cast<int>(copy_len);
    }

    void inject(uint32_t src_ip, std::vector<uint8_t> bytes) {
        IncomingPacket pkt;
        pkt.bytes    = std::move(bytes);
        pkt.src_ip   = src_ip;
        pkt.src_port = SYNC_PORT;
        incoming.push_back(std::move(pkt));
    }

    std::vector<SentPacket>    sent;
    std::deque<IncomingPacket> incoming;

    bool force_bind_fail  = false;
    bool force_send_fail  = false;
    bool force_recv_error = false;

    int  bind_count  = 0;
    int  close_count = 0;

private:
    bool     _bound      = false;
    uint16_t _bound_port = 0;
};

// ---- Wire helpers ----------------------------------------------------------

struct ParsedPing {
    uint32_t device_boot_token;
    uint32_t seq;
    int64_t  t1;
};

ParsedPing parse_ping(const std::vector<uint8_t>& bytes) {
    REQUIRE(bytes.size() == 33);
    REQUIRE(bytes[0] == 0x01);
    ParsedPing p;
    std::memcpy(&p.device_boot_token, bytes.data() + 17, 4);
    std::memcpy(&p.seq,               bytes.data() + 21, 4);
    std::memcpy(&p.t1,                bytes.data() + 25, 8);
    return p;
}

std::vector<uint8_t> build_pong(uint32_t controller_token, uint32_t seq,
                                int64_t t1, int64_t t2, int64_t t3)
{
    std::vector<uint8_t> pkt(33, 0);
    pkt[0] = 0x02;
    std::memcpy(pkt.data() + 1,  &controller_token, 4);
    std::memcpy(pkt.data() + 5,  &seq,              4);
    std::memcpy(pkt.data() + 9,  &t1,               8);
    std::memcpy(pkt.data() + 17, &t2,               8);
    std::memcpy(pkt.data() + 25, &t3,               8);
    return pkt;
}

// One round-trip: poll() to send PING, advance clock, inject PONG, poll()
// to drain. controller_offset_us is added to t1 to derive t2 / t3 (so the
// "controller is X us ahead" relationship holds exactly).
void exchange_round(ClockSyncClient& client, FakeUdpTransport& udp,
                    uint32_t controller_ip, uint32_t controller_token,
                    int64_t controller_offset_us, int64_t rtt_us = 1000)
{
    const size_t before = udp.sent.size();
    client.poll();
    REQUIRE(udp.sent.size() == before + 1);

    const ParsedPing ping = parse_ping(udp.sent.back().bytes);

    const int64_t t2 = ping.t1 + controller_offset_us + rtt_us / 2;
    const int64_t t3 = t2 + 10;  // 10us controller "processing"
    advance_test_us(rtt_us);

    udp.inject(controller_ip, build_pong(controller_token,
                                         ping.seq, ping.t1, t2, t3));
    client.poll();
}

DeviceIdentity make_identity(uint32_t boot_token = 0xDEADBEEF) {
    DeviceIdentity id;
    std::strncpy(id.uid, "sim-test-uid", UID_BUF_SIZE - 1);
    id.boot_token       = boot_token;
    id.protocol_version = PROTOCOL_VERSION;
    return id;
}

}  // namespace

// ---------------------------------------------------------------------------
// Target set / clear
// ---------------------------------------------------------------------------

TEST_CASE("set_controller(0) at construction is the idle path",
          "[clock_sync_client]") {
    set_test_now_us(1'000'000);

    SyncedClock      clock;
    DeviceIdentity   id  = make_identity();
    FakeUdpTransport udp;
    ClockSyncClient  client(udp, clock, id);

    client.poll();
    CHECK(udp.sent.empty());
    CHECK(udp.bind_count == 0);
    CHECK_FALSE(clock.is_synced());
}

TEST_CASE("set_controller(non-zero) starts a burst and binds lazily",
          "[clock_sync_client]") {
    set_test_now_us(1'000'000);

    SyncedClock      clock;
    DeviceIdentity   id  = make_identity();
    FakeUdpTransport udp;
    ClockSyncClient  client(udp, clock, id);

    client.set_controller(CONTROLLER_IP_A);

    // Bind happens inside poll(), not inside set_controller.
    CHECK(udp.bind_count == 0);

    client.poll();
    CHECK(udp.bind_count == 1);
    REQUIRE(udp.sent.size() == 1);

    const auto& sent = udp.sent.back();
    CHECK(sent.dst_ip   == CONTROLLER_IP_A);
    CHECK(sent.dst_port == SYNC_PORT);
    CHECK(sent.bytes[0] == 0x01);  // PING
}

TEST_CASE("set_controller(0) closes UDP and leaves SyncedClock alone",
          "[clock_sync_client]") {
    set_test_now_us(1'000'000);

    SyncedClock      clock;
    DeviceIdentity   id  = make_identity();
    FakeUdpTransport udp;
    ClockSyncClient  client(udp, clock, id);

    client.set_controller(CONTROLLER_IP_A);

    // Three accepted rounds is exactly MIN_SAMPLES_TO_APPLY; the lease
    // applies on the third and the burst exits.
    for (int i = 0; i < 3; ++i) {
        exchange_round(client, udp, CONTROLLER_IP_A, 0xC0FFEEU,
                       /*offset*/ 1'000'000);
        advance_test_us(BURST_INTERVAL_US);
    }
    REQUIRE(clock.is_synced());

    const int closes_before = udp.close_count;
    client.set_controller(0);

    CHECK(udp.close_count == closes_before + 1);
    CHECK_FALSE(udp.is_bound());

    // Lease left intact; transient drop policy.
    CHECK(clock.is_synced());

    // Idle: poll() neither rebinds nor sends.
    const size_t sent_before = udp.sent.size();
    client.poll();
    CHECK(udp.sent.size() == sent_before);
    CHECK_FALSE(udp.is_bound());
}

TEST_CASE("changing target IP resets filter and restarts burst",
          "[clock_sync_client]") {
    set_test_now_us(1'000'000);

    SyncedClock      clock;
    DeviceIdentity   id  = make_identity();
    FakeUdpTransport udp;
    ClockSyncClient  client(udp, clock, id);

    client.set_controller(CONTROLLER_IP_A);
    for (int i = 0; i < 3; ++i) {
        exchange_round(client, udp, CONTROLLER_IP_A, 0xC0FFEEU,
                       /*offset*/ 1'000'000);
        advance_test_us(BURST_INTERVAL_US);
    }
    REQUIRE(clock.is_synced());

    // Switch peers. set_controller resets filter; SyncedClock still
    // holds its lease (transient-drop semantics).
    const size_t sent_before = udp.sent.size();
    client.set_controller(CONTROLLER_IP_B);
    CHECK(clock.is_synced());

    client.poll();
    REQUIRE(udp.sent.size() == sent_before + 1);
    CHECK(udp.sent.back().dst_ip == CONTROLLER_IP_B);

    // Burst is back: the next ping is due at +500 ms, not +15 s.
    advance_test_us(BURST_INTERVAL_US);
    client.poll();
    CHECK(udp.sent.size() == sent_before + 2);
}

// ---------------------------------------------------------------------------
// PING wire content
// ---------------------------------------------------------------------------

TEST_CASE("first PING carries the identity boot_token",
          "[clock_sync_client]") {
    set_test_now_us(1'000'000);

    SyncedClock      clock;
    DeviceIdentity   id  = make_identity(0x12345678U);
    FakeUdpTransport udp;
    ClockSyncClient  client(udp, clock, id);

    client.set_controller(CONTROLLER_IP_A);
    client.poll();

    REQUIRE(udp.sent.size() == 1);
    const ParsedPing ping = parse_ping(udp.sent.back().bytes);
    CHECK(ping.device_boot_token == 0x12345678U);
}

// ---------------------------------------------------------------------------
// Controller boot token (remote epoch)
// ---------------------------------------------------------------------------

TEST_CASE("first PONG seeds controller_boot_token and is processed",
          "[clock_sync_client]") {
    set_test_now_us(1'000'000);

    SyncedClock      clock;
    DeviceIdentity   id  = make_identity();
    FakeUdpTransport udp;
    ClockSyncClient  client(udp, clock, id);

    client.set_controller(CONTROLLER_IP_A);

    // Three matched rounds with a stable controller_boot_token apply a
    // lease (MIN_SAMPLES_TO_APPLY = 3).
    for (int i = 0; i < 3; ++i) {
        exchange_round(client, udp, CONTROLLER_IP_A, 0xAAAAU,
                       /*offset*/ 1'000'000, /*rtt*/ 1000);
        advance_test_us(BURST_INTERVAL_US);
    }

    REQUIRE(clock.is_synced());
}

TEST_CASE("controller_boot_token change clears SyncedClock and discards PONG",
          "[clock_sync_client]") {
    set_test_now_us(1'000'000);

    SyncedClock      clock;
    DeviceIdentity   id  = make_identity();
    FakeUdpTransport udp;
    ClockSyncClient  client(udp, clock, id);

    client.set_controller(CONTROLLER_IP_A);
    for (int i = 0; i < 3; ++i) {
        exchange_round(client, udp, CONTROLLER_IP_A, 0xAAAAU,
                       /*offset*/ 1'000'000);
        advance_test_us(BURST_INTERVAL_US);
    }
    REQUIRE(clock.is_synced());

    // Force a fresh PING after the lease has applied: schedule is now
    // on STEADY cadence, so advance past STEADY_INTERVAL_US to make
    // the next ping due, then poll() and use THAT round's seq/t1 in
    // the changed-token reply. Round-first matching means a token
    // change carried by an already-consumed round wouldn't trigger
    // anything.
    advance_test_us(STEADY_INTERVAL_US);
    const size_t sent_before_change = udp.sent.size();
    client.poll();
    REQUIRE(udp.sent.size() == sent_before_change + 1);

    const ParsedPing ping = parse_ping(udp.sent.back().bytes);

    advance_test_us(1000);
    udp.inject(CONTROLLER_IP_A,
               build_pong(0xBBBBU, ping.seq, ping.t1,
                          ping.t1 + 1'500'000, ping.t1 + 1'500'010));
    client.poll();

    CHECK_FALSE(clock.is_synced());

    // Epoch change started a fresh burst: the same poll() that
    // detected the change also fired the immediate-due ping.
    CHECK(udp.sent.size() == sent_before_change + 2);
}

// ---------------------------------------------------------------------------
// PONG validation gates
// ---------------------------------------------------------------------------

TEST_CASE("PONG with wrong source IP is discarded",
          "[clock_sync_client]") {
    set_test_now_us(1'000'000);

    SyncedClock      clock;
    DeviceIdentity   id  = make_identity();
    FakeUdpTransport udp;
    ClockSyncClient  client(udp, clock, id);

    client.set_controller(CONTROLLER_IP_A);
    client.poll();
    REQUIRE(!udp.sent.empty());
    const ParsedPing ping = parse_ping(udp.sent.back().bytes);

    advance_test_us(1000);
    udp.inject(WRONG_IP, build_pong(0xAAAAU, ping.seq, ping.t1,
                                    ping.t1 + 1'000'000,
                                    ping.t1 + 1'000'010));
    client.poll();

    // No sample landed: not synced after one rejected reply.
    CHECK_FALSE(clock.is_synced());
}

TEST_CASE("PONG with wrong size or type is discarded",
          "[clock_sync_client]") {
    set_test_now_us(1'000'000);

    SyncedClock      clock;
    DeviceIdentity   id  = make_identity();
    FakeUdpTransport udp;
    ClockSyncClient  client(udp, clock, id);

    client.set_controller(CONTROLLER_IP_A);
    client.poll();
    REQUIRE(!udp.sent.empty());
    const ParsedPing ping = parse_ping(udp.sent.back().bytes);

    // Wrong size (truncated)
    std::vector<uint8_t> short_pong = build_pong(0xAAAAU, ping.seq, ping.t1,
                                                  ping.t1 + 1'000'000,
                                                  ping.t1 + 1'000'010);
    short_pong.resize(20);
    udp.inject(CONTROLLER_IP_A, std::move(short_pong));

    // Wrong type byte
    std::vector<uint8_t> wrong_type = build_pong(0xAAAAU, ping.seq, ping.t1,
                                                  ping.t1 + 1'000'000,
                                                  ping.t1 + 1'000'010);
    wrong_type[0] = 0x99;
    udp.inject(CONTROLLER_IP_A, std::move(wrong_type));

    advance_test_us(1000);
    client.poll();

    CHECK_FALSE(clock.is_synced());
}

TEST_CASE("PONG with wrong seq or t1 is discarded",
          "[clock_sync_client]") {
    set_test_now_us(1'000'000);

    SyncedClock      clock;
    DeviceIdentity   id  = make_identity();
    FakeUdpTransport udp;
    ClockSyncClient  client(udp, clock, id);

    client.set_controller(CONTROLLER_IP_A);
    client.poll();
    REQUIRE(!udp.sent.empty());
    const ParsedPing ping = parse_ping(udp.sent.back().bytes);

    // Wrong seq (off by one)
    udp.inject(CONTROLLER_IP_A,
               build_pong(0xAAAAU, ping.seq + 1, ping.t1,
                          ping.t1 + 1'000'000, ping.t1 + 1'000'010));
    // Wrong t1 (echoed back as something we didn't send)
    udp.inject(CONTROLLER_IP_A,
               build_pong(0xAAAAU, ping.seq, ping.t1 + 999,
                          ping.t1 + 1'000'000, ping.t1 + 1'000'010));

    advance_test_us(1000);
    client.poll();

    CHECK_FALSE(clock.is_synced());
}

TEST_CASE("PONG with RTT past the gate is dropped",
          "[clock_sync_client]") {
    set_test_now_us(1'000'000);

    SyncedClock      clock;
    DeviceIdentity   id  = make_identity();
    FakeUdpTransport udp;
    ClockSyncClient  client(udp, clock, id);

    client.set_controller(CONTROLLER_IP_A);

    // Three rounds where each round's RTT exceeds RTT_GATE_US (200ms).
    for (int i = 0; i < 3; ++i) {
        client.poll();
        REQUIRE(!udp.sent.empty());
        const ParsedPing ping = parse_ping(udp.sent.back().bytes);
        const int64_t huge_rtt = 500 * 1000;  // 500ms > 200ms gate
        advance_test_us(huge_rtt);
        udp.inject(CONTROLLER_IP_A,
                   build_pong(0xAAAAU, ping.seq, ping.t1,
                              ping.t1 + 100'000, ping.t1 + 100'010));
        client.poll();
        advance_test_us(BURST_INTERVAL_US);
    }

    CHECK_FALSE(clock.is_synced());
}

// ---------------------------------------------------------------------------
// Lease application and offset sign
// ---------------------------------------------------------------------------

TEST_CASE("three accepted rounds apply a lease with the correct sign",
          "[clock_sync_client]") {
    set_test_now_us(1'000'000);

    SyncedClock      clock;
    DeviceIdentity   id  = make_identity();
    FakeUdpTransport udp;
    ClockSyncClient  client(udp, clock, id);

    client.set_controller(CONTROLLER_IP_A);

    // Controller is 1,000,000 us ahead of device.
    for (int i = 0; i < 3; ++i) {
        exchange_round(client, udp, CONTROLLER_IP_A, 0xAAAAU,
                       /*offset_remote_minus_local*/ 1'000'000,
                       /*rtt*/ 1000);
        advance_test_us(BURST_INTERVAL_US);
    }

    REQUIRE(clock.is_synced());

    // SyncedClock convention: stored offset = local - remote, so the
    // device's now_local_us() - now_remote_us() is the stored value.
    // With remote = local + 1,000,000, expect stored offset ~= -1,000,000.
    const int64_t stored = clock.now_local_us() - clock.now_remote_us();
    CHECK(stored < 0);
    CHECK(stored > -1'010'000);
    CHECK(stored < -990'000);
}

// ---------------------------------------------------------------------------
// Burst lifecycle
// ---------------------------------------------------------------------------

TEST_CASE("burst exits on the first applied lease",
          "[clock_sync_client]") {
    set_test_now_us(1'000'000);

    SyncedClock      clock;
    DeviceIdentity   id  = make_identity();
    FakeUdpTransport udp;
    ClockSyncClient  client(udp, clock, id);

    client.set_controller(CONTROLLER_IP_A);

    // Three rounds get a lease while in burst mode.
    for (int i = 0; i < 3; ++i) {
        exchange_round(client, udp, CONTROLLER_IP_A, 0xAAAAU,
                       /*offset*/ 1'000'000);
        advance_test_us(BURST_INTERVAL_US);
    }
    REQUIRE(clock.is_synced());

    // After the lease, the next ping should be on STEADY cadence.
    // Advance just past BURST_INTERVAL_US: not enough for the next
    // ping if we're now on 15 s.
    const size_t sent_before = udp.sent.size();
    advance_test_us(BURST_INTERVAL_US + 1);
    client.poll();
    CHECK(udp.sent.size() == sent_before);

    // Advance through the 15 s steady interval; ping fires.
    advance_test_us(STEADY_INTERVAL_US);
    client.poll();
    CHECK(udp.sent.size() == sent_before + 1);
}

TEST_CASE("burst exits on deadline if no replies come back",
          "[clock_sync_client]") {
    set_test_now_us(1'000'000);

    SyncedClock      clock;
    DeviceIdentity   id  = make_identity();
    FakeUdpTransport udp;
    ClockSyncClient  client(udp, clock, id);

    client.set_controller(CONTROLLER_IP_A);

    // Pump pings without any replies. After BURST_DURATION_US of
    // burst-spaced pings, the schedule should fall back to STEADY.
    int pings_in_window = 0;
    while (true) {
        const size_t before = udp.sent.size();
        client.poll();
        if (udp.sent.size() > before) pings_in_window += 1;
        advance_test_us(BURST_INTERVAL_US);
        if (now_us() > 1'000'000 + BURST_DURATION_US) break;
    }

    // Burst should have produced multiple pings (~20 at 500ms over 10s).
    CHECK(pings_in_window > 5);

    // Now we are past the burst deadline. Advancing a second BURST
    // interval should NOT fire another ping; we are on STEADY now.
    const size_t after_burst = udp.sent.size();
    advance_test_us(BURST_INTERVAL_US);
    client.poll();
    CHECK(udp.sent.size() == after_burst);

    // Steady cadence: a 15 s advance fires the next ping.
    advance_test_us(STEADY_INTERVAL_US);
    client.poll();
    CHECK(udp.sent.size() == after_burst + 1);
}

// ---------------------------------------------------------------------------
// Send / recv error paths
// ---------------------------------------------------------------------------

TEST_CASE("send failure closes the socket, next tick rebinds",
          "[clock_sync_client]") {
    set_test_now_us(1'000'000);

    SyncedClock      clock;
    DeviceIdentity   id  = make_identity();
    FakeUdpTransport udp;
    ClockSyncClient  client(udp, clock, id);

    client.set_controller(CONTROLLER_IP_A);

    udp.force_send_fail = true;
    client.poll();

    CHECK(udp.sent.empty());
    CHECK_FALSE(udp.is_bound());
    CHECK(udp.bind_count == 1);

    // Recover and pump again. Next tick re-binds and sends.
    udp.force_send_fail = false;
    client.poll();
    CHECK(udp.bind_count == 2);
    CHECK(udp.sent.size() == 1);
}

TEST_CASE("recv error closes the socket and skips this tick's send",
          "[clock_sync_client]") {
    set_test_now_us(1'000'000);

    SyncedClock      clock;
    DeviceIdentity   id  = make_identity();
    FakeUdpTransport udp;
    ClockSyncClient  client(udp, clock, id);

    client.set_controller(CONTROLLER_IP_A);
    client.poll();
    REQUIRE(udp.sent.size() == 1);

    // Advance so a ping is scheduled, then trigger a recv error.
    advance_test_us(BURST_INTERVAL_US);
    udp.force_recv_error = true;
    const size_t sent_before = udp.sent.size();
    client.poll();

    CHECK_FALSE(udp.is_bound());
    CHECK(udp.sent.size() == sent_before);

    // Next tick rebinds and sends.
    client.poll();
    CHECK(udp.is_bound());
    CHECK(udp.sent.size() == sent_before + 1);
}
