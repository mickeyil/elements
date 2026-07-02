#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <cstring>
#include <string>
#include <vector>

#include "device_identity.h"
#include "discovery.h"
#include "log_shipper.h"
#include "slog.h"
#include "test_platform_clock.h"
#include "udp_transport.h"

namespace {

constexpr uint32_t CONTROLLER_IP = 0x0100007F;  // 127.0.0.1, network order
constexpr uint16_t EXPECTED_LOG_PORT = 6044;

class FakeUdpTransport : public UdpTransport
{
public:
    bool bind(uint16_t) override { bound = true; return true; }
    void close() override { bound = false; }
    bool is_bound() const override { return bound; }

    bool send(const uint8_t* src, size_t len,
              uint32_t dst_ip, uint16_t dst_port) override
    {
        if (!send_ok) return false;
        sent.push_back(std::vector<uint8_t>(src, src + len));
        last_dst_ip = dst_ip;
        last_dst_port = dst_port;
        return true;
    }

    int recv(uint8_t* dst, size_t n, uint32_t* src_ip, uint16_t* src_port) override
    {
        if (inbox.empty()) return 0;
        const std::vector<uint8_t> pkt = inbox.front();
        inbox.erase(inbox.begin());
        const size_t take = std::min(n, pkt.size());
        std::memcpy(dst, pkt.data(), take);
        *src_ip = CONTROLLER_IP;
        *src_port = 6040;
        return static_cast<int>(take);
    }

    bool bound = false;
    bool send_ok = true;
    uint32_t last_dst_ip = 0;
    uint16_t last_dst_port = 0;
    std::vector<std::vector<uint8_t>> inbox;
    std::vector<std::vector<uint8_t>> sent;
};

std::vector<uint8_t> make_offer(uint32_t ip, uint16_t port)
{
    std::vector<uint8_t> pkt = {0xCC, 0xD1, 0x02};  // magic LE, PKT_OFFER
    const uint8_t* ip_bytes = reinterpret_cast<const uint8_t*>(&ip);
    pkt.insert(pkt.end(), ip_bytes, ip_bytes + 4);
    pkt.push_back(port & 0xFF);
    pkt.push_back((port >> 8) & 0xFF);
    return pkt;
}

void put_u32(std::vector<uint8_t>& b, uint32_t v)
{
    for (int i = 0; i < 4; ++i) b.push_back((v >> (8 * i)) & 0xFF);
}

// The datagram the shipper must produce for one record.
std::vector<uint8_t> expected_datagram(const char* uid, uint32_t boot_token,
                                       uint32_t seq, uint32_t uptime_ms,
                                       uint8_t level, const std::string& text)
{
    std::vector<uint8_t> pkt = {0x6C, 0xD1, 0x01};  // magic LE, version
    uint8_t slot[UID_SIZE] = {};
    std::memcpy(slot, uid, std::strlen(uid));
    pkt.insert(pkt.end(), slot, slot + sizeof(slot));
    put_u32(pkt, boot_token);
    put_u32(pkt, seq);
    put_u32(pkt, uptime_ms);
    pkt.push_back(level);
    pkt.insert(pkt.end(), text.begin(), text.end());
    return pkt;
}

struct Harness
{
    DeviceIdentity identity;
    FakeUdpTransport discovery_udp;
    FakeUdpTransport log_udp;
    DiscoveryClient discovery{discovery_udp, identity};
    LogShipper shipper{log_udp, discovery, identity};

    int64_t now = 1'000'000;

    Harness()
    {
        std::strcpy(identity.uid, "sim-log");
        identity.boot_token = 0xAABBCCDD;
        set_test_now_us(now);
        slog_reset();
    }

    void advance(int64_t delta_us)
    {
        now += delta_us;
        set_test_now_us(now);
    }

    void offer()
    {
        discovery_udp.inbox.push_back(make_offer(CONTROLLER_IP, 6041));
        discovery.poll();
    }
};

}  // namespace

TEST_CASE("nothing ships before any OFFER arrives")
{
    Harness h;
    slog_info("early");
    h.shipper.tick(false);
    h.shipper.tick(true);   // no controller address either way

    CHECK(h.log_udp.sent.empty());
    CHECK_FALSE(h.log_udp.bound);   // gate closed: no needless bind

    SlogRecord rec;
    CHECK(slog_peek(rec));   // still buffered
}

TEST_CASE("a fresh OFFER opens the gate and records ship encoded")
{
    Harness h;
    h.advance(2'345'000);   // uptime 3.345 s at log time
    slog_warn("wifi flaky");
    h.offer();

    h.shipper.tick(false);

    REQUIRE(h.log_udp.sent.size() == 1);
    CHECK(h.log_udp.last_dst_ip == CONTROLLER_IP);
    CHECK(h.log_udp.last_dst_port == EXPECTED_LOG_PORT);
    CHECK(h.log_udp.sent[0] ==
          expected_datagram("sim-log", 0xAABBCCDD, 1, 3'345,
                            SLOG_LEVEL_WARN, "wifi flaky"));
    CHECK(h.log_udp.bound);

    SlogRecord rec;
    CHECK_FALSE(slog_peek(rec));   // shipped records leave the ring
}

TEST_CASE("a stale OFFER holds records until the link stands in")
{
    Harness h;
    h.offer();
    h.advance(4'000'000);   // past the freshness window
    slog_info("buffered through the outage");

    h.shipper.tick(false);
    CHECK(h.log_udp.sent.empty());   // stale and no link: hold

    h.shipper.tick(true);            // link up: controller evidently alive
    REQUIRE(h.log_udp.sent.size() == 1);
}

TEST_CASE("sends are capped per tick; the backlog drains across ticks")
{
    Harness h;
    h.offer();
    for (int i = 0; i < 6; ++i) slog_info("msg %d", i);

    h.shipper.tick(true);
    CHECK(h.log_udp.sent.size() == 4);

    h.shipper.tick(true);
    CHECK(h.log_udp.sent.size() == 6);

    SlogRecord rec;
    CHECK_FALSE(slog_peek(rec));
}

TEST_CASE("a failed send keeps the record for the next tick")
{
    Harness h;
    h.offer();
    slog_error("must not vanish");

    h.log_udp.send_ok = false;
    h.shipper.tick(true);
    CHECK(h.log_udp.sent.empty());

    SlogRecord rec;
    REQUIRE(slog_peek(rec));   // still buffered

    h.log_udp.send_ok = true;
    h.shipper.tick(true);
    REQUIRE(h.log_udp.sent.size() == 1);
    CHECK_FALSE(slog_peek(rec));
}
