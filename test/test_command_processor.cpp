#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <cstring>
#include <map>
#include <string>
#include <vector>

#include "animation_store.h"
#include "app_context.h"
#include "command_handler.h"
#include "command_processor.h"
#include "device_status.h"
#include "key_value_store.h"
#include "link_protocol.h"
#include "playback.h"
#include "system_platform.h"
#include "tcp_transport.h"
#include "test_platform_clock.h"

namespace {

// ---------------------------------------------------------------------------
// Fakes
// ---------------------------------------------------------------------------

// In-memory byte pipe. `inbox` is what poll() will read (optionally in
// chunks of max_read_chunk); `sent` collects ACK messages.
class FakeTcpTransport : public TcpTransport
{
public:
    bool connect(uint32_t, uint16_t) override { connected = true; return true; }
    void disconnect() override { connected = false; }
    bool is_connected() const override { return connected; }

    int read(uint8_t* dst, size_t n) override
    {
        if (!connected || read_error) return -1;
        if (inbox.empty()) return 0;
        const size_t take = std::min({n, inbox.size(), max_read_chunk});
        std::memcpy(dst, inbox.data(), take);
        inbox.erase(inbox.begin(), inbox.begin() + take);
        return static_cast<int>(take);
    }

    bool write(const uint8_t* src, size_t len) override
    {
        if (!connected || fail_writes) {
            connected = false;
            return false;
        }
        sent.insert(sent.end(), src, src + len);
        return true;
    }

    bool connected = true;
    bool read_error = false;
    bool fail_writes = false;
    size_t max_read_chunk = SIZE_MAX;
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
// Message helpers
// ---------------------------------------------------------------------------

void put_u32(std::vector<uint8_t>& b, uint32_t v)
{
    for (int i = 0; i < 4; ++i) b.push_back((v >> (8 * i)) & 0xFF);
}

std::vector<uint8_t> make_msg(uint8_t opcode,
                              const std::vector<uint8_t>& payload = {})
{
    std::vector<uint8_t> f;
    put_u32(f, static_cast<uint32_t>(1 + payload.size()));
    f.push_back(opcode);
    f.insert(f.end(), payload.begin(), payload.end());
    return f;
}

std::vector<uint8_t> make_ack(uint8_t status,
                              const std::vector<uint8_t>& payload = {})
{
    std::vector<uint8_t> f;
    put_u32(f, static_cast<uint32_t>(2 + payload.size()));
    f.push_back(CMD_ACK);
    f.push_back(status);
    f.insert(f.end(), payload.begin(), payload.end());
    return f;
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
    FakeTcpTransport tcp;
    CommandProcessor processor{tcp, handler};

    Harness() { set_test_now_us(0); }

    void feed(const std::vector<uint8_t>& bytes)
    {
        tcp.inbox.insert(tcp.inbox.end(), bytes.begin(), bytes.end());
    }
};

}  // namespace

TEST_CASE("poll is Idle with nothing to read")
{
    Harness h;
    CHECK(h.processor.poll() == PollResult::Idle);
    CHECK(h.tcp.sent.empty());
}

TEST_CASE("a ping message is handled and ACKed Ok")
{
    Harness h;
    h.feed(make_msg(CMD_PING));
    CHECK(h.processor.poll() == PollResult::Handled);
    CHECK(h.tcp.sent == make_ack(ACK_OK));
}

TEST_CASE("a message arriving in small chunks buffers until complete")
{
    Harness h;
    h.tcp.max_read_chunk = 2;
    h.feed(make_msg(CMD_PING));  // 6 bytes -> 3 reads

    CHECK(h.processor.poll() == PollResult::Idle);   // 2 bytes
    CHECK(h.processor.poll() == PollResult::Idle);   // 4 bytes
    CHECK(h.processor.poll() == PollResult::Handled);
    CHECK(h.tcp.sent == make_ack(ACK_OK));
}

TEST_CASE("two buffered messages are handled one per poll")
{
    Harness h;
    h.feed(make_msg(CMD_PING));
    h.feed(make_msg(CMD_PING));

    CHECK(h.processor.poll() == PollResult::Handled);
    CHECK(h.tcp.sent.size() == make_ack(ACK_OK).size());
    CHECK(h.processor.poll() == PollResult::Handled);
    CHECK(h.tcp.sent.size() == 2 * make_ack(ACK_OK).size());
    CHECK(h.processor.poll() == PollResult::Idle);
}

TEST_CASE("zero and oversized message lengths fault")
{
    Harness h;
    h.feed({0x00, 0x00, 0x00, 0x00});  // length 0
    CHECK(h.processor.poll() == PollResult::Fault);

    Harness h2;
    std::vector<uint8_t> oversize;
    put_u32(oversize, TCP_MSG_MAX - 4 + 1);
    h2.feed(oversize);
    CHECK(h2.processor.poll() == PollResult::Fault);
}

TEST_CASE("a transport read error faults")
{
    Harness h;
    h.tcp.read_error = true;
    CHECK(h.processor.poll() == PollResult::Fault);
}

TEST_CASE("an unknown opcode ACKs UnknownCommand and the stream continues")
{
    Harness h;
    h.feed(make_msg(0x7F));
    h.feed(make_msg(CMD_PING));

    CHECK(h.processor.poll() == PollResult::Handled);
    CHECK(h.processor.poll() == PollResult::Handled);

    std::vector<uint8_t> expected = make_ack(ACK_UNKNOWN_COMMAND);
    const auto ping_ack = make_ack(ACK_OK);
    expected.insert(expected.end(), ping_ack.begin(), ping_ack.end());
    CHECK(h.tcp.sent == expected);
}

TEST_CASE("a query reply payload rides inside the ACK message")
{
    Harness h;
    h.status.mode = DeviceMode::DetachedBackground;
    h.status.flags = 0x01;
    h.feed(make_msg(CMD_QUERY_DEVICE_STATUS));

    CHECK(h.processor.poll() == PollResult::Handled);
    CHECK(h.tcp.sent ==
          make_ack(ACK_OK, {MODE_DETACHED_BACKGROUND, 0x01, 0x00, 0x00}));
}

TEST_CASE("a near-max message is consumed and the stream continues")
{
    Harness h;
    // A 12 KiB garbage LOAD: a well-formed message, rejected by the decoder.
    std::vector<uint8_t> garbage(12 * 1024, 0xAB);
    h.feed(make_msg(CMD_LOAD, garbage));
    h.feed(make_msg(CMD_PING));

    CHECK(h.processor.poll() == PollResult::Handled);
    CHECK(h.processor.poll() == PollResult::Handled);

    std::vector<uint8_t> expected = make_ack(ACK_ERROR);
    const auto ping_ack = make_ack(ACK_OK);
    expected.insert(expected.end(), ping_ack.begin(), ping_ack.end());
    CHECK(h.tcp.sent == expected);
}

TEST_CASE("reset_buffer drops a partial message")
{
    Harness h;
    h.tcp.max_read_chunk = 3;
    h.feed({0xFF, 0xFF, 0xFF});             // garbage prefix, would misparse
    CHECK(h.processor.poll() == PollResult::Idle);

    h.processor.reset_buffer();
    h.tcp.max_read_chunk = SIZE_MAX;
    h.feed(make_msg(CMD_PING));
    CHECK(h.processor.poll() == PollResult::Handled);
    CHECK(h.tcp.sent == make_ack(ACK_OK));
}

TEST_CASE("a failed ACK write is best-effort; the next poll faults")
{
    Harness h;
    h.tcp.fail_writes = true;
    h.feed(make_msg(CMD_PING));

    CHECK(h.processor.poll() == PollResult::Handled);  // message consumed
    CHECK(h.tcp.sent.empty());
    CHECK(h.processor.poll() == PollResult::Fault);    // transport now dead
}
