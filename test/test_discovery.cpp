#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <cstring>
#include <deque>
#include <vector>

#include "../src/device_identity.h"
#include "../src/discovery.h"
#include "../src/udp_transport.h"
#include "test_platform_clock.h"

// What is and is not tested here.
//
// In scope (testable without a real network):
//   - initial state (controller_ip / tcp_port both 0; no bind)
//   - lazy bind to ephemeral on the first poll()
//   - DISCOVER wire shape (magic, type, uid, dst, port)
//   - configurable dst_ip (sim path)
//   - broadcast interval gating
//   - OFFER parsing populates controller_ip / tcp_port
//   - latest OFFER overwrites previous
//   - malformed OFFERs (wrong size, magic, type) are ignored
//   - bind / send / recv failures close the socket and retry next tick
//
// Out of scope (deferred until controller-side discovery server lands;
// requires a real UDP peer):
//   - end-to-end discovery against a Python controller
//   - LAN broadcast behavior on real hardware

namespace {

constexpr uint16_t DISCOVERY_PORT       = 6040;
constexpr int64_t  BROADCAST_INTERVAL_US = 1'500'000;
constexpr uint16_t MAGIC                = 0xD1CC;

constexpr uint32_t LOOPBACK_BE          = 0x0100007FU;  // 127.0.0.1
constexpr uint32_t CONTROLLER_IP_A      = 0x0100A8C0U;  // 192.168.0.1
constexpr uint32_t CONTROLLER_IP_B      = 0x0200A8C0U;  // 192.168.0.2
constexpr uint16_t TCP_PORT_A           = 6041;
constexpr uint16_t TCP_PORT_B           = 7777;

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

    void inject(std::vector<uint8_t> bytes) {
        IncomingPacket pkt;
        pkt.bytes    = std::move(bytes);
        pkt.src_ip   = CONTROLLER_IP_A;
        pkt.src_port = DISCOVERY_PORT;
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

std::vector<uint8_t> build_offer(uint32_t ip, uint16_t port,
                                 uint16_t magic = MAGIC,
                                 uint8_t  type  = 0x02)
{
    std::vector<uint8_t> pkt(9, 0);
    std::memcpy(pkt.data(),     &magic, 2);
    pkt[2] = type;
    std::memcpy(pkt.data() + 3, &ip,    4);
    std::memcpy(pkt.data() + 7, &port,  2);
    return pkt;
}

DeviceIdentity make_identity(const char* uid = "sim-test-uid") {
    DeviceIdentity id;
    std::strncpy(id.uid, uid, UID_BUF_SIZE - 1);
    id.boot_token = 0xDEADBEEF;
    return id;
}

}  // namespace

// ---------------------------------------------------------------------------
// Initial state
// ---------------------------------------------------------------------------

TEST_CASE("controller_ip and tcp_port are zero before first OFFER",
          "[discovery]") {
    set_test_now_us(1'000'000);

    DeviceIdentity   id  = make_identity();
    FakeUdpTransport udp;
    DiscoveryClient  d(udp, id);

    CHECK(d.controller_ip() == 0u);
    CHECK(d.tcp_port()      == 0u);
    CHECK(udp.bind_count    == 0);
}

TEST_CASE("first poll() lazy-binds on an ephemeral port", "[discovery]") {
    set_test_now_us(1'000'000);

    DeviceIdentity   id  = make_identity();
    FakeUdpTransport udp;
    DiscoveryClient  d(udp, id);

    d.poll();

    CHECK(udp.bind_count == 1);
    CHECK(udp.is_bound());
    REQUIRE(udp.sent.size() == 1);
}

// ---------------------------------------------------------------------------
// DISCOVER wire shape
// ---------------------------------------------------------------------------

TEST_CASE("DISCOVER carries magic, type, and uid", "[discovery]") {
    set_test_now_us(1'000'000);

    DeviceIdentity   id  = make_identity("sim-foo");
    FakeUdpTransport udp;
    DiscoveryClient  d(udp, id);

    d.poll();

    REQUIRE(udp.sent.size() == 1);
    const auto& bytes = udp.sent.back().bytes;
    REQUIRE(bytes.size() == 19);

    uint16_t magic = 0;
    std::memcpy(&magic, bytes.data(), 2);
    CHECK(magic == MAGIC);
    CHECK(bytes[2] == 0x01);

    // uid: "sim-foo" then null-padded out to 16 bytes.
    CHECK(std::memcmp(bytes.data() + 3, "sim-foo", 7) == 0);
    for (size_t i = 10; i < 19; ++i) CHECK(bytes[i] == 0);
}

TEST_CASE("DISCOVER goes to broadcast on default dst_ip", "[discovery]") {
    set_test_now_us(1'000'000);

    DeviceIdentity   id  = make_identity();
    FakeUdpTransport udp;
    DiscoveryClient  d(udp, id);

    d.poll();

    REQUIRE(udp.sent.size() == 1);
    CHECK(udp.sent.back().dst_ip   == IPV4_BROADCAST);
    CHECK(udp.sent.back().dst_port == DISCOVERY_PORT);
}

TEST_CASE("DISCOVER honors a configured dst_ip (sim loopback path)",
          "[discovery]") {
    set_test_now_us(1'000'000);

    DeviceIdentity   id  = make_identity();
    FakeUdpTransport udp;
    DiscoveryClient  d(udp, id, LOOPBACK_BE);

    d.poll();

    REQUIRE(udp.sent.size() == 1);
    CHECK(udp.sent.back().dst_ip == LOOPBACK_BE);
}

// ---------------------------------------------------------------------------
// Broadcast interval
// ---------------------------------------------------------------------------

TEST_CASE("polls within one interval do not produce a second DISCOVER",
          "[discovery]") {
    set_test_now_us(1'000'000);

    DeviceIdentity   id  = make_identity();
    FakeUdpTransport udp;
    DiscoveryClient  d(udp, id);

    d.poll();
    REQUIRE(udp.sent.size() == 1);

    advance_test_us(BROADCAST_INTERVAL_US - 1);
    d.poll();
    CHECK(udp.sent.size() == 1);

    advance_test_us(2);
    d.poll();
    CHECK(udp.sent.size() == 2);
}

// ---------------------------------------------------------------------------
// OFFER parsing
// ---------------------------------------------------------------------------

TEST_CASE("a valid OFFER populates controller_ip and tcp_port",
          "[discovery]") {
    set_test_now_us(1'000'000);

    DeviceIdentity   id  = make_identity();
    FakeUdpTransport udp;
    DiscoveryClient  d(udp, id);

    d.poll();
    udp.inject(build_offer(CONTROLLER_IP_A, TCP_PORT_A));
    d.poll();

    CHECK(d.controller_ip() == CONTROLLER_IP_A);
    CHECK(d.tcp_port()      == TCP_PORT_A);
}

TEST_CASE("the most recent OFFER wins", "[discovery]") {
    set_test_now_us(1'000'000);

    DeviceIdentity   id  = make_identity();
    FakeUdpTransport udp;
    DiscoveryClient  d(udp, id);

    d.poll();
    udp.inject(build_offer(CONTROLLER_IP_A, TCP_PORT_A));
    udp.inject(build_offer(CONTROLLER_IP_B, TCP_PORT_B));
    d.poll();

    CHECK(d.controller_ip() == CONTROLLER_IP_B);
    CHECK(d.tcp_port()      == TCP_PORT_B);
}

TEST_CASE("malformed OFFERs are ignored", "[discovery]") {
    set_test_now_us(1'000'000);

    DeviceIdentity   id  = make_identity();
    FakeUdpTransport udp;
    DiscoveryClient  d(udp, id);

    d.poll();

    SECTION("wrong size") {
        std::vector<uint8_t> too_short(8, 0xAB);
        std::vector<uint8_t> too_long(10, 0xAB);
        udp.inject(std::move(too_short));
        udp.inject(std::move(too_long));
    }
    SECTION("wrong magic") {
        udp.inject(build_offer(CONTROLLER_IP_A, TCP_PORT_A,
                               /*magic*/ 0xBEEF));
    }
    SECTION("wrong type byte") {
        udp.inject(build_offer(CONTROLLER_IP_A, TCP_PORT_A,
                               /*magic*/ MAGIC, /*type*/ 0x01));
    }

    d.poll();

    CHECK(d.controller_ip() == 0u);
    CHECK(d.tcp_port()      == 0u);
}

// ---------------------------------------------------------------------------
// Failure paths
// ---------------------------------------------------------------------------

TEST_CASE("bind failure is silent and retried next tick", "[discovery]") {
    set_test_now_us(1'000'000);

    DeviceIdentity   id  = make_identity();
    FakeUdpTransport udp;
    DiscoveryClient  d(udp, id);

    udp.force_bind_fail = true;
    d.poll();
    CHECK_FALSE(udp.is_bound());
    CHECK(udp.sent.empty());

    udp.force_bind_fail = false;
    d.poll();
    CHECK(udp.is_bound());
    CHECK(udp.sent.size() == 1);
}

TEST_CASE("send failure closes the socket", "[discovery]") {
    set_test_now_us(1'000'000);

    DeviceIdentity   id  = make_identity();
    FakeUdpTransport udp;
    DiscoveryClient  d(udp, id);

    udp.force_send_fail = true;
    d.poll();
    CHECK(udp.close_count == 1);
    CHECK_FALSE(udp.is_bound());
    CHECK(udp.sent.empty());
}

TEST_CASE("recv error closes the socket", "[discovery]") {
    set_test_now_us(1'000'000);

    DeviceIdentity   id  = make_identity();
    FakeUdpTransport udp;
    DiscoveryClient  d(udp, id);

    d.poll();
    REQUIRE(udp.is_bound());
    const int closes_before = udp.close_count;

    udp.force_recv_error = true;
    d.poll();
    CHECK(udp.close_count == closes_before + 1);
    CHECK_FALSE(udp.is_bound());
}

TEST_CASE("has_fresh_offer tracks OFFER recency", "[discovery]") {
    set_test_now_us(1'000'000);

    DeviceIdentity   id  = make_identity();
    FakeUdpTransport udp;
    DiscoveryClient  d(udp, id);

    CHECK_FALSE(d.has_fresh_offer());

    udp.inject(build_offer(CONTROLLER_IP_A, TCP_PORT_A));
    d.poll();
    CHECK(d.has_fresh_offer());

    // Two broadcast intervals is the freshness window.
    set_test_now_us(1'000'000 + 2 * BROADCAST_INTERVAL_US);
    CHECK(d.has_fresh_offer());

    set_test_now_us(1'000'000 + 2 * BROADCAST_INTERVAL_US + 1);
    CHECK_FALSE(d.has_fresh_offer());

    // A new OFFER makes the values fresh again.
    udp.inject(build_offer(CONTROLLER_IP_A, TCP_PORT_A));
    d.poll();
    CHECK(d.has_fresh_offer());
}
