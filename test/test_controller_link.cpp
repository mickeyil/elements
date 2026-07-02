#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <cstring>
#include <map>
#include <string>
#include <vector>

#include "animation_store.h"
#include "app_context.h"
#include "command_handler.h"
#include "controller_link.h"
#include "device_identity.h"
#include "device_status.h"
#include "discovery.h"
#include "key_value_store.h"
#include "link_protocol.h"
#include "network_interface.h"
#include "playback.h"
#include "system_platform.h"
#include "tcp_transport.h"
#include "test_platform_clock.h"
#include "udp_transport.h"

namespace {

constexpr uint32_t CONTROLLER_IP = 0x0100007F;  // 127.0.0.1, network order
constexpr uint16_t CONTROLLER_PORT = 6041;

// ---------------------------------------------------------------------------
// Fakes
// ---------------------------------------------------------------------------

class FakeNetworkInterface : public NetworkInterface
{
public:
    void begin() override {}
    NetworkTransition poll() override { return NetworkTransition::unchanged; }
    bool is_up() const override { return up; }
    bool up = true;
};

// Delivers queued packets to DiscoveryClient; sends are dropped.
class FakeUdpTransport : public UdpTransport
{
public:
    bool bind(uint16_t) override { bound = true; return true; }
    void close() override { bound = false; }
    bool is_bound() const override { return bound; }
    bool send(const uint8_t*, size_t, uint32_t, uint16_t) override { return true; }

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
    std::vector<std::vector<uint8_t>> inbox;
};

class FakeTcpTransport : public TcpTransport
{
public:
    bool connect(uint32_t ip, uint16_t port) override
    {
        ++connect_calls;
        last_ip = ip;
        last_port = port;
        if (!accept_connect) return false;
        connected = true;
        return true;
    }

    void disconnect() override { connected = false; }
    bool is_connected() const override { return connected; }

    int read(uint8_t* dst, size_t n) override
    {
        if (!connected || read_error) return -1;
        if (inbox.empty()) return 0;
        const size_t take = std::min(n, inbox.size());
        std::memcpy(dst, inbox.data(), take);
        inbox.erase(inbox.begin(), inbox.begin() + take);
        return static_cast<int>(take);
    }

    int write(const uint8_t* src, size_t len) override
    {
        if (!connected || fail_writes) {
            connected = false;
            return -1;
        }
        const size_t take = std::min(len, write_budget);
        if (take == 0) return 0;   // "buffer full"
        write_budget -= take;
        sent.insert(sent.end(), src, src + take);
        return static_cast<int>(take);
    }

    bool connected = false;
    bool accept_connect = true;
    bool read_error = false;
    bool fail_writes = false;
    // Bytes the socket accepts before reporting "buffer full" (0).
    size_t write_budget = SIZE_MAX;
    int connect_calls = 0;
    uint32_t last_ip = 0;
    uint16_t last_port = 0;
    std::vector<uint8_t> inbox;
    std::vector<uint8_t> sent;
};

class FakeFileStore : public FileStore
{
public:
    FileStoreState state() const override { return FileStoreState::Ready; }
    bool write(const char* name, const uint8_t* src, size_t len) override
    {
        _files[name].assign(src, src + len);
        return true;
    }
    int size(const char* name) override
    {
        auto it = _files.find(name);
        return it == _files.end() ? -1 : static_cast<int>(it->second.size());
    }
    int read(const char* name, uint8_t* dst, size_t max_len) override
    {
        auto it = _files.find(name);
        if (it == _files.end()) return -1;
        const size_t n = std::min(max_len, it->second.size());
        std::memcpy(dst, it->second.data(), n);
        return static_cast<int>(n);
    }
    bool remove(const char* name) override { return _files.erase(name) > 0; }

private:
    std::map<std::string, std::vector<uint8_t>> _files;
};

class FakeKeyValueStore : public KeyValueStore
{
public:
    KeyValueStoreState state() const override { return KeyValueStoreState::Ready; }
    bool has_key(const char*) override { return false; }
    bool remove(const char*) override { return false; }
    bool get_u8(const char*, uint8_t&) override { return false; }
    bool get_u16(const char*, uint16_t&) override { return false; }
    bool get_f32(const char*, float&) override { return false; }
    bool put_u8(const char*, uint8_t) override { return true; }
    bool put_u16(const char*, uint16_t) override { return true; }
    bool put_f32(const char*, float) override { return true; }
    bool put_str(const char*, const char*) override { return true; }
    int get_str(const char*, char*, size_t) override { return -1; }
};

class FakeSystemPlatform : public SystemPlatform
{
public:
    void reboot() override {}
};

// ---------------------------------------------------------------------------
// Packet / message builders
// ---------------------------------------------------------------------------

std::vector<uint8_t> make_offer(uint32_t ip, uint16_t port)
{
    std::vector<uint8_t> pkt = {0xCC, 0xD1, 0x02};  // magic LE, PKT_OFFER
    const uint8_t* ip_bytes = reinterpret_cast<const uint8_t*>(&ip);
    pkt.insert(pkt.end(), ip_bytes, ip_bytes + 4);
    pkt.push_back(port & 0xFF);
    pkt.push_back((port >> 8) & 0xFF);
    return pkt;
}

std::vector<uint8_t> make_msg(uint8_t opcode)
{
    return {0x01, 0x00, 0x00, 0x00, opcode};
}

std::vector<uint8_t> expected_register(const char* uid, uint32_t boot_token)
{
    std::vector<uint8_t> msg = {22, 0x00, 0x00, 0x00, CMD_REGISTER};
    uint8_t slot[UID_SIZE] = {};
    std::memcpy(slot, uid, std::strlen(uid));
    msg.insert(msg.end(), slot, slot + sizeof(slot));
    for (int i = 0; i < 4; ++i) msg.push_back((boot_token >> (8 * i)) & 0xFF);
    msg.push_back(PROTOCOL_VERSION);
    return msg;
}

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

struct Harness
{
    SyncedClock clock;
    Playback playback{1, clock};
    FakeFileStore files;
    AnimationStore animations{files};
    FakeKeyValueStore kv;
    DeviceStatus status;
    FakeSystemPlatform system;
    AppContext ctx{playback, animations, kv, status, system};
    CommandHandler handler{ctx};

    DeviceIdentity identity;
    FakeNetworkInterface net;
    FakeUdpTransport udp;
    DiscoveryClient discovery{udp, identity};
    FakeTcpTransport tcp;
    ControllerLink link{net, discovery, tcp, identity, handler};

    int64_t now = 1'000'000;

    Harness()
    {
        std::strcpy(identity.uid, "sim-link");
        identity.boot_token = 0x11223344;
        set_test_now_us(now);
    }

    void advance(int64_t delta_us)
    {
        now += delta_us;
        set_test_now_us(now);
    }

    void offer() { udp.inbox.push_back(make_offer(CONTROLLER_IP, CONTROLLER_PORT)); }

    void establish()
    {
        offer();
        link.poll();
        REQUIRE(link.is_ready());
        tcp.sent.clear();
    }
};

}  // namespace

TEST_CASE("network down: nothing happens")
{
    Harness h;
    h.net.up = false;
    h.offer();
    h.link.poll();

    CHECK_FALSE(h.link.is_ready());
    CHECK(h.tcp.connect_calls == 0);
    CHECK_FALSE(h.udp.bound);  // discovery not even polled
}

TEST_CASE("discovering without an OFFER stays detached")
{
    Harness h;
    h.link.poll();
    CHECK_FALSE(h.link.is_ready());
    CHECK(h.tcp.connect_calls == 0);
    CHECK(h.udp.bound);  // discovery is being polled
}

TEST_CASE("an OFFER connects, registers, and attaches in one tick")
{
    Harness h;
    h.offer();
    h.link.poll();

    CHECK(h.link.is_ready());
    CHECK(h.tcp.connect_calls == 1);
    CHECK(h.tcp.last_ip == CONTROLLER_IP);
    CHECK(h.tcp.last_port == CONTROLLER_PORT);
    CHECK(h.tcp.sent == expected_register("sim-link", 0x11223344));
    CHECK(h.link.controller_ip_addr() == CONTROLLER_IP);
}

TEST_CASE("failed connects are paced by the retry interval")
{
    Harness h;
    h.tcp.accept_connect = false;
    h.offer();

    h.link.poll();
    CHECK(h.tcp.connect_calls == 1);

    h.advance(CONNECT_RETRY_INTERVAL_MS * 1000 / 2);
    h.link.poll();
    CHECK(h.tcp.connect_calls == 1);  // still cooling down

    h.advance(CONNECT_RETRY_INTERVAL_MS * 1000);
    h.link.poll();
    CHECK(h.tcp.connect_calls == 2);
    CHECK_FALSE(h.link.is_ready());
}

TEST_CASE("a failed REGISTER write tears down and retries later")
{
    Harness h;
    h.tcp.fail_writes = true;
    h.offer();
    h.link.poll();

    CHECK_FALSE(h.link.is_ready());
    CHECK_FALSE(h.tcp.is_connected());

    h.tcp.fail_writes = false;
    h.advance(CONNECT_RETRY_INTERVAL_MS * 1000 + 1);
    h.link.poll();
    CHECK(h.link.is_ready());
}

TEST_CASE("a short REGISTER write counts as a failed connect")
{
    // A fresh socket's send buffer takes 26 bytes whole or the
    // connection is broken; the link must not come up half-registered.
    Harness h;
    h.tcp.write_budget = 5;
    h.offer();
    h.link.poll();

    CHECK_FALSE(h.link.is_ready());
    CHECK_FALSE(h.tcp.is_connected());

    h.tcp.write_budget = SIZE_MAX;
    h.advance(CONNECT_RETRY_INTERVAL_MS * 1000 + 1);
    h.link.poll();
    CHECK(h.link.is_ready());
}

TEST_CASE("handled messages keep the link alive; silence drops it")
{
    Harness h;
    h.establish();

    // Just inside the deadline: a ping refreshes it.
    h.advance(PING_TIMEOUT_MS * 1000 - 1'000'000);
    h.tcp.inbox = make_msg(CMD_PING);
    h.link.poll();
    REQUIRE(h.link.is_ready());

    // Again just inside the refreshed deadline: still alive.
    h.advance(PING_TIMEOUT_MS * 1000 - 1'000'000);
    h.link.poll();
    CHECK(h.link.is_ready());

    // Past the deadline with no traffic: dropped.
    h.advance(2'000'000);
    h.link.poll();
    CHECK_FALSE(h.link.is_ready());
    CHECK_FALSE(h.tcp.is_connected());
    CHECK(h.link.controller_ip_addr() == 0);
}

TEST_CASE("a processor fault tears down; the cached OFFER reattaches")
{
    Harness h;
    h.establish();

    h.tcp.inbox = {0x00, 0x00, 0x00, 0x00};  // zero-length message
    h.link.poll();
    CHECK_FALSE(h.link.is_ready());
    CHECK_FALSE(h.tcp.is_connected());

    // The discovery values are still cached; the next tick reconnects.
    h.tcp.inbox.clear();
    h.link.poll();
    REQUIRE(h.link.is_ready());

    // The fresh stream parses cleanly from its start.
    h.tcp.sent.clear();
    h.tcp.inbox = make_msg(CMD_PING);
    h.link.poll();
    CHECK(h.link.is_ready());
    CHECK_FALSE(h.tcp.sent.empty());
}

TEST_CASE("the link stops connecting once the OFFER goes stale")
{
    Harness h;
    h.tcp.accept_connect = false;
    h.offer();
    h.link.poll();
    CHECK(h.tcp.connect_calls == 1);

    // The controller stopped announcing; past the freshness window the
    // cached address is left alone.
    h.advance(4'000'000);
    h.link.poll();
    CHECK(h.tcp.connect_calls == 1);

    // A new OFFER resumes connecting.
    h.tcp.accept_connect = true;
    h.offer();
    h.link.poll();
    CHECK(h.tcp.connect_calls == 2);
    CHECK(h.link.is_ready());
}

TEST_CASE("a read error while attached tears down")
{
    Harness h;
    h.establish();
    h.tcp.read_error = true;
    h.link.poll();
    CHECK_FALSE(h.link.is_ready());
    CHECK_FALSE(h.tcp.is_connected());
}

TEST_CASE("network loss while attached tears down; return rediscovers")
{
    Harness h;
    h.establish();

    h.net.up = false;
    h.link.poll();
    CHECK_FALSE(h.link.is_ready());
    CHECK_FALSE(h.tcp.is_connected());
    CHECK(h.link.controller_ip_addr() == 0);

    h.net.up = true;
    h.link.poll();  // cached OFFER, immediate reconnect
    CHECK(h.link.is_ready());
}

TEST_CASE("a dead socket noticed while attached tears down")
{
    Harness h;
    h.establish();
    h.tcp.connected = false;  // peer vanished between ticks
    h.link.poll();
    CHECK_FALSE(h.link.is_ready());
}
