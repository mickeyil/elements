#include <catch2/catch_test_macros.hpp>

#include <arpa/inet.h>

#include <chrono>
#include <cstdint>
#include <cstring>
#include <thread>
#include <vector>

#include "../src/posix_udp_transport.h"

using controller_link::PosixUdpTransport;

namespace {

constexpr uint32_t LOOPBACK_BE = 0x0100007F;  // 127.0.0.1 in network byte order

// Drain helper: poll the socket briefly so a just-sent datagram has time
// to land. Loopback delivery is effectively immediate, but recvfrom is
// non-blocking, so a tight retry loop with a tiny sleep keeps tests
// stable without depending on kernel scheduling.
int drain_once(PosixUdpTransport& t,
               uint8_t* dst, size_t n,
               uint32_t* src_ip, uint16_t* src_port)
{
    for (int i = 0; i < 100; ++i) {
        const int r = t.recv(dst, n, src_ip, src_port);
        if (r != 0) return r;
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    return 0;
}

}  // namespace

TEST_CASE("Fresh transport is not bound", "[udp_transport]") {
    PosixUdpTransport t;
    CHECK_FALSE(t.is_bound());
    CHECK(t.local_port() == 0);

    uint8_t buf[8] = {};
    uint32_t ip = 0;
    uint16_t port = 0;
    CHECK(t.recv(buf, sizeof(buf), &ip, &port) == -1);
    CHECK_FALSE(t.send(buf, sizeof(buf), LOOPBACK_BE, 1234));
}

TEST_CASE("bind(0) assigns an ephemeral port and exposes it", "[udp_transport]") {
    PosixUdpTransport t;
    REQUIRE(t.bind(0));
    CHECK(t.is_bound());
    CHECK(t.local_port() != 0);
}

TEST_CASE("close releases the port and is idempotent", "[udp_transport]") {
    PosixUdpTransport t;
    REQUIRE(t.bind(0));
    t.close();
    CHECK_FALSE(t.is_bound());
    CHECK(t.local_port() == 0);
    t.close();  // second close: no crash, no state change
    CHECK_FALSE(t.is_bound());
}

TEST_CASE("rebind rules use the actual bound port", "[udp_transport]") {
    PosixUdpTransport t;
    REQUIRE(t.bind(0));
    const uint16_t actual = t.local_port();
    REQUIRE(actual != 0);

    SECTION("bind matching the kernel-assigned port no-ops") {
        CHECK(t.bind(actual));
        CHECK(t.local_port() == actual);
    }

    SECTION("bind(0) while bound no-ops") {
        CHECK(t.bind(0));
        CHECK(t.local_port() == actual);  // unchanged
    }

    SECTION("bind to a different non-zero port fails") {
        const uint16_t different = (actual == 50000) ? 50001 : 50000;
        CHECK_FALSE(t.bind(different));
        CHECK(t.is_bound());
        CHECK(t.local_port() == actual);
    }
}

TEST_CASE("close then rebind to the same port succeeds", "[udp_transport]") {
    PosixUdpTransport a;
    REQUIRE(a.bind(0));
    const uint16_t port = a.local_port();
    a.close();

    PosixUdpTransport b;
    REQUIRE(b.bind(port));
    CHECK(b.local_port() == port);
}

TEST_CASE("recv on empty socket returns 0 (no useful datagram)", "[udp_transport]") {
    PosixUdpTransport t;
    REQUIRE(t.bind(0));
    uint8_t buf[8] = {};
    uint32_t ip = 0;
    uint16_t port = 0;
    CHECK(t.recv(buf, sizeof(buf), &ip, &port) == 0);
}

TEST_CASE("send/recv round trip populates source address", "[udp_transport]") {
    PosixUdpTransport sender;
    PosixUdpTransport receiver;
    REQUIRE(sender.bind(0));
    REQUIRE(receiver.bind(0));
    const uint16_t sender_port   = sender.local_port();
    const uint16_t receiver_port = receiver.local_port();

    const uint8_t payload[] = {0xDE, 0xAD, 0xBE, 0xEF, 0x42};
    REQUIRE(sender.send(payload, sizeof(payload),
                        LOOPBACK_BE, receiver_port));

    uint8_t buf[64] = {};
    uint32_t src_ip = 0;
    uint16_t src_port = 0;
    const int n = drain_once(receiver, buf, sizeof(buf), &src_ip, &src_port);
    REQUIRE(n == static_cast<int>(sizeof(payload)));
    CHECK(std::memcmp(buf, payload, sizeof(payload)) == 0);
    CHECK(src_ip == LOOPBACK_BE);
    CHECK(src_port == sender_port);
}

TEST_CASE("multi-datagram drain returns each in turn", "[udp_transport]") {
    PosixUdpTransport sender;
    PosixUdpTransport receiver;
    REQUIRE(sender.bind(0));
    REQUIRE(receiver.bind(0));
    const uint16_t receiver_port = receiver.local_port();

    const uint8_t a[] = {1, 2, 3};
    const uint8_t b[] = {4, 5};
    const uint8_t c[] = {6};
    REQUIRE(sender.send(a, sizeof(a), LOOPBACK_BE, receiver_port));
    REQUIRE(sender.send(b, sizeof(b), LOOPBACK_BE, receiver_port));
    REQUIRE(sender.send(c, sizeof(c), LOOPBACK_BE, receiver_port));

    uint8_t buf[64] = {};
    uint32_t ip = 0;
    uint16_t port = 0;

    int n = drain_once(receiver, buf, sizeof(buf), &ip, &port);
    REQUIRE(n == 3);
    CHECK(std::memcmp(buf, a, 3) == 0);
    n = drain_once(receiver, buf, sizeof(buf), &ip, &port);
    REQUIRE(n == 2);
    CHECK(std::memcmp(buf, b, 2) == 0);
    n = drain_once(receiver, buf, sizeof(buf), &ip, &port);
    REQUIRE(n == 1);
    CHECK(buf[0] == c[0]);

    // Now empty.
    CHECK(receiver.recv(buf, sizeof(buf), &ip, &port) == 0);
}

TEST_CASE("recv truncates oversized datagrams", "[udp_transport]") {
    PosixUdpTransport sender;
    PosixUdpTransport receiver;
    REQUIRE(sender.bind(0));
    REQUIRE(receiver.bind(0));
    const uint16_t receiver_port = receiver.local_port();

    uint8_t big[64];
    for (size_t i = 0; i < sizeof(big); ++i) big[i] = static_cast<uint8_t>(i);
    REQUIRE(sender.send(big, sizeof(big), LOOPBACK_BE, receiver_port));

    uint8_t small[8] = {};
    uint32_t ip = 0;
    uint16_t port = 0;
    const int n = drain_once(receiver, small, sizeof(small), &ip, &port);
    REQUIRE(n == static_cast<int>(sizeof(small)));
    for (size_t i = 0; i < sizeof(small); ++i) CHECK(small[i] == i);

    // The remainder of the datagram is discarded by the kernel; the
    // socket should now report no data.
    CHECK(receiver.recv(small, sizeof(small), &ip, &port) == 0);
}

TEST_CASE("zero-byte datagram is consumed and folded onto no-data", "[udp_transport]") {
    PosixUdpTransport sender;
    PosixUdpTransport receiver;
    REQUIRE(sender.bind(0));
    REQUIRE(receiver.bind(0));
    const uint16_t receiver_port = receiver.local_port();

    REQUIRE(sender.send(nullptr, 0, LOOPBACK_BE, receiver_port));

    uint8_t buf[8] = {};
    uint32_t ip = 0;
    uint16_t port = 0;
    // Per the contract: the empty datagram is consumed and recv returns
    // 0 ("no useful datagram"). A follow-up unicast still gets through.
    (void)drain_once(receiver, buf, sizeof(buf), &ip, &port);

    const uint8_t real[] = {0x77};
    REQUIRE(sender.send(real, sizeof(real), LOOPBACK_BE, receiver_port));
    const int n = drain_once(receiver, buf, sizeof(buf), &ip, &port);
    REQUIRE(n == 1);
    CHECK(buf[0] == 0x77);
}

TEST_CASE("send rejects payloads above the per-datagram cap", "[udp_transport]") {
    PosixUdpTransport sender;
    REQUIRE(sender.bind(0));

    // The cap mirrors WiFiUDP's tx-buffer flush boundary; POSIX enforces
    // it too so both impls obey the same one-call/one-datagram contract.
    std::vector<uint8_t> too_big(controller_link::MAX_PAYLOAD_SIZE + 1, 0xAB);
    CHECK_FALSE(sender.send(too_big.data(), too_big.size(), LOOPBACK_BE, 65000));
    // Right at the cap is allowed (delivery itself isn't checked here;
    // a 1460-byte loopback datagram is fine on Linux/macOS).
    std::vector<uint8_t> at_cap(controller_link::MAX_PAYLOAD_SIZE, 0xCD);
    CHECK(sender.send(at_cap.data(), at_cap.size(), LOOPBACK_BE, 65000));
}

TEST_CASE("send fails when not bound", "[udp_transport]") {
    PosixUdpTransport t;
    const uint8_t one = 1;
    CHECK_FALSE(t.send(&one, 1, LOOPBACK_BE, 65000));

    REQUIRE(t.bind(0));
    t.close();
    CHECK_FALSE(t.send(&one, 1, LOOPBACK_BE, 65000));
}
