#include <catch2/catch_test_macros.hpp>

#include <cstring>
#include <vector>

#include "device_identity.h"
#include "sim_frame_output.h"
#include "strip.h"
#include "udp_transport.h"

namespace {

constexpr uint32_t DST_IP = 0x0100007F;  // 127.0.0.1, network order
constexpr uint16_t DST_PORT = 6042;

class FakeUdpTransport : public UdpTransport
{
public:
    bool bind(uint16_t) override
    {
        ++bind_calls;
        if (!accept_bind) return false;
        bound = true;
        return true;
    }

    void close() override { bound = false; }
    bool is_bound() const override { return bound; }

    bool send(const uint8_t* src, size_t len,
              uint32_t dst_ip, uint16_t dst_port) override
    {
        if (!bound || fail_sends) return false;
        last_ip = dst_ip;
        last_port = dst_port;
        sent.emplace_back(src, src + len);
        return true;
    }

    int recv(uint8_t*, size_t, uint32_t*, uint16_t*) override { return 0; }

    bool bound = false;
    bool accept_bind = true;
    bool fail_sends = false;
    int bind_calls = 0;
    uint32_t last_ip = 0;
    uint16_t last_port = 0;
    std::vector<std::vector<uint8_t>> sent;
};

DeviceIdentity named_identity()
{
    DeviceIdentity id;
    std::strcpy(id.uid, "sim-pv");
    return id;
}

struct Harness
{
    FakeUdpTransport udp;
    DeviceIdentity identity = named_identity();
    Strip strip;
    SimFrameOutput out{udp, identity, DST_IP, DST_PORT};

    Harness()
    {
        REQUIRE(strip.resize(2));
        strip[0] = rgb_t(255, 0, 0);
        strip[1] = rgb_t(1, 2, 3);
    }
};

std::vector<uint8_t> expected_packet(uint32_t frame_index, float t_program,
                                     const Strip& strip)
{
    std::vector<uint8_t> pkt(UID_SIZE, 0);
    std::memcpy(pkt.data(), "sim-pv", 6);
    for (int i = 0; i < 4; ++i) pkt.push_back((frame_index >> (8 * i)) & 0xFF);
    uint8_t f[4];
    std::memcpy(f, &t_program, 4);
    pkt.insert(pkt.end(), f, f + 4);
    pkt.insert(pkt.end(), strip.bytes(), strip.bytes() + strip.byte_size());
    return pkt;
}

}  // namespace

TEST_CASE("write sends one packet with header and raw RGB")
{
    Harness h;

    h.out.write(h.strip, 0.5f);

    REQUIRE(h.udp.sent.size() == 1);
    CHECK(h.udp.sent[0] == expected_packet(0, 0.5f, h.strip));
    CHECK(h.udp.last_ip == DST_IP);
    CHECK(h.udp.last_port == DST_PORT);
}

TEST_CASE("the frame index increments per write")
{
    Harness h;

    h.out.write(h.strip, 0.0f);
    h.out.write(h.strip, 0.1f);

    REQUIRE(h.udp.sent.size() == 2);
    CHECK(h.udp.sent[1] == expected_packet(1, 0.1f, h.strip));
}

TEST_CASE("the socket binds lazily and only once")
{
    Harness h;
    CHECK(h.udp.bind_calls == 0);

    h.out.write(h.strip, 0.0f);
    h.out.write(h.strip, 0.1f);
    CHECK(h.udp.bind_calls == 1);
}

TEST_CASE("a failed send closes the socket; the next write recovers")
{
    Harness h;

    h.udp.fail_sends = true;
    h.out.write(h.strip, 0.0f);
    CHECK_FALSE(h.udp.is_bound());
    CHECK(h.udp.sent.empty());

    // The dropped frame still consumed an index; the receiver sees a gap.
    h.udp.fail_sends = false;
    h.out.write(h.strip, 0.1f);
    REQUIRE(h.udp.sent.size() == 1);
    CHECK(h.udp.sent[0] == expected_packet(1, 0.1f, h.strip));
}

TEST_CASE("a failed bind drops the frame without sending")
{
    Harness h;

    h.udp.accept_bind = false;
    h.out.write(h.strip, 0.0f);
    CHECK(h.udp.sent.empty());

    h.udp.accept_bind = true;
    h.out.write(h.strip, 0.1f);
    REQUIRE(h.udp.sent.size() == 1);
    CHECK(h.udp.sent[0] == expected_packet(1, 0.1f, h.strip));
}
