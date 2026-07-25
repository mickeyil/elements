#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <cstring>
#include <map>
#include <string>
#include <vector>

#include "app/animation_store.h"
#include "core/animation_types.h"
#include "app/app.h"
#include "platform/device_identity.h"
#include "app/discovery.h"
#include "platform/file_store.h"
#include "platform/frame_output.h"
#include "app/hardware_profile_store.h"
#include "platform/key_value_store.h"
#include "app/link_protocol.h"
#include "platform/network_interface.h"
#include "platform/platform_clock.h"
#include "core/runtime_constants.h"
#include "platform/system_platform.h"
#include "platform/tcp_transport.h"
#include "test_platform_clock.h"
#include "platform/udp_transport.h"

namespace {

constexpr uint32_t CONTROLLER_IP = 0x0100007F;  // 127.0.0.1, network order
constexpr uint16_t CONTROLLER_PORT = 6041;

// ---------------------------------------------------------------------------
// Fakes
// ---------------------------------------------------------------------------

class FakeNetworkInterface : public NetInterface
{
public:
    void begin() override {}
    NetworkTransition poll() override { return NetworkTransition::unchanged; }
    bool is_up() const override { return up; }
    bool up = true;
};

// Delivers queued packets; records sends.
class FakeUdpTransport : public UdpTransport
{
public:
    bool bind(uint16_t) override { bound = true; return true; }
    void close() override { bound = false; }
    bool is_bound() const override { return bound; }

    bool send(const uint8_t* src, size_t len, uint32_t, uint16_t) override
    {
        sent.emplace_back(src, src + len);
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
    std::vector<std::vector<uint8_t>> inbox;
    std::vector<std::vector<uint8_t>> sent;
};

class FakeTcpTransport : public TcpTransport
{
public:
    bool connect(uint32_t ip, uint16_t port) override
    {
        last_ip = ip;
        last_port = port;
        connected = true;
        return true;
    }

    void disconnect() override { connected = false; }
    bool is_connected() const override { return connected; }

    int read(uint8_t* dst, size_t n) override
    {
        if (!connected) return -1;
        if (inbox.empty()) return 0;
        const size_t take = std::min(n, inbox.size());
        std::memcpy(dst, inbox.data(), take);
        inbox.erase(inbox.begin(), inbox.begin() + take);
        return static_cast<int>(take);
    }

    int write(const uint8_t* src, size_t len) override
    {
        // Models time passing while the App retries a full socket, so
        // a drain loop against a frozen test clock still hits its
        // deadline instead of spinning forever.
        if (advance_us_on_write != 0) {
            set_test_now_us(now_us() + advance_us_on_write);
        }
        if (!connected) return -1;
        if (stalled_writes > 0) {
            --stalled_writes;
            return 0;   // "buffer full"
        }
        sent.insert(sent.end(), src, src + len);
        return static_cast<int>(len);
    }

    bool connected = false;
    int stalled_writes = 0;   // next N write calls accept nothing
    int64_t advance_us_on_write = 0;
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

// Map-backed key-value store, so begin() has a real profile to load.
class MemoryKeyValueStore : public KeyValueStore
{
public:
    KeyValueStoreState state() const override { return KeyValueStoreState::Ready; }
    bool has_key(const char* k) override
    {
        return _u8.count(k) || _u16.count(k) || _f32.count(k) || _str.count(k);
    }
    bool remove(const char* k) override
    {
        return _u8.erase(k) + _u16.erase(k) + _f32.erase(k) + _str.erase(k) > 0;
    }
    bool get_u8(const char* k, uint8_t& out) override { return get_(_u8, k, out); }
    bool get_u16(const char* k, uint16_t& out) override { return get_(_u16, k, out); }
    bool get_f32(const char* k, float& out) override { return get_(_f32, k, out); }
    bool put_u8(const char* k, uint8_t v) override { _u8[k] = v; return true; }
    bool put_u16(const char* k, uint16_t v) override { _u16[k] = v; return true; }
    bool put_f32(const char* k, float v) override { _f32[k] = v; return true; }
    bool put_str(const char* k, const char* v) override { _str[k] = v; return true; }
    int get_str(const char* k, char* out, size_t out_cap) override
    {
        auto it = _str.find(k);
        if (it == _str.end() || it->second.size() + 1 > out_cap) return -1;
        std::strcpy(out, it->second.c_str());
        return static_cast<int>(it->second.size());
    }

private:
    template <typename T>
    static bool get_(const std::map<std::string, T>& m, const char* k, T& out)
    {
        auto it = m.find(k);
        if (it == m.end()) return false;
        out = it->second;
        return true;
    }

    std::map<std::string, uint8_t>  _u8;
    std::map<std::string, uint16_t> _u16;
    std::map<std::string, float>    _f32;
    std::map<std::string, std::string> _str;
};

// Snapshots the TCP send stream at reboot time, so a test can prove
// the ACK was on the wire before the reboot fired.
class RecordingSystemPlatform : public SystemPlatform
{
public:
    explicit RecordingSystemPlatform(FakeTcpTransport& tcp) : _tcp(tcp) {}
    void reboot() override
    {
        ++reboots;
        sent_at_reboot = _tcp.sent;
    }

    int reboots = 0;
    std::vector<uint8_t> sent_at_reboot;

private:
    FakeTcpTransport& _tcp;
};

class RecordingFrameOutput : public FrameOutput
{
public:
    struct Frame
    {
        uint8_t r = 0, g = 0, b = 0;  // pixel 0
        float t_program = 0.0f;
    };

    void apply_profile(const HardwareProfile& profile) override
    {
        ++profiles_applied;
        profile_strip_length = profile.strip_length;
    }

    void write(const Strip& strip, float t_program) override
    {
        Frame f;
        if (strip.size() > 0) {
            f.r = strip[0].r;
            f.g = strip[0].g;
            f.b = strip[0].b;
        }
        f.t_program = t_program;
        frames.push_back(f);
    }

    int profiles_applied = 0;
    uint16_t profile_strip_length = 0;
    std::vector<Frame> frames;
};

bool is_red(const RecordingFrameOutput::Frame& f)
{
    return f.r == 255 && f.g == 0 && f.b == 0;
}

bool is_black(const RecordingFrameOutput::Frame& f)
{
    return f.r == 0 && f.g == 0 && f.b == 0;
}

// ---------------------------------------------------------------------------
// Packet / message / blob builders
// ---------------------------------------------------------------------------

void put_u8(std::vector<uint8_t>& b, uint8_t v) { b.push_back(v); }
void put_u16(std::vector<uint8_t>& b, uint16_t v)
{
    b.push_back(v & 0xFF);
    b.push_back((v >> 8) & 0xFF);
}
void put_u32(std::vector<uint8_t>& b, uint32_t v)
{
    for (int i = 0; i < 4; ++i) b.push_back((v >> (8 * i)) & 0xFF);
}
void put_i64(std::vector<uint8_t>& b, int64_t v)
{
    for (int i = 0; i < 8; ++i) b.push_back((static_cast<uint64_t>(v) >> (8 * i)) & 0xFF);
}
void put_f32(std::vector<uint8_t>& b, float f)
{
    uint8_t bytes[4];
    std::memcpy(bytes, &f, 4);
    b.insert(b.end(), bytes, bytes + 4);
}

std::vector<uint8_t> make_offer(uint32_t ip, uint16_t port)
{
    std::vector<uint8_t> pkt = {0xCC, 0xD1, 0x02};  // magic LE, PKT_OFFER
    const uint8_t* ip_bytes = reinterpret_cast<const uint8_t*>(&ip);
    pkt.insert(pkt.end(), ip_bytes, ip_bytes + 4);
    pkt.push_back(port & 0xFF);
    pkt.push_back((port >> 8) & 0xFF);
    return pkt;
}

std::vector<uint8_t> make_msg(uint8_t opcode,
                              const std::vector<uint8_t>& payload = {})
{
    std::vector<uint8_t> msg;
    put_u32(msg, static_cast<uint32_t>(1 + payload.size()));
    put_u8(msg, opcode);
    msg.insert(msg.end(), payload.begin(), payload.end());
    return msg;
}

// Outbound ACK with status Ok and no reply payload.
std::vector<uint8_t> ack_ok()
{
    return {0x02, 0x00, 0x00, 0x00, CMD_ACK, 0x00};
}

// 32-byte wire name slot, null-padded.
std::vector<uint8_t> name_slot(const char* name)
{
    std::vector<uint8_t> slot(ANIM_NAME_SIZE, 0);
    std::memcpy(slot.data(), name, std::strlen(name));
    return slot;
}

// Solid-red paint event covering [0, duration). Strip length = 1.
std::vector<uint8_t> build_paint_blob(float duration, uint8_t target_fps = 50)
{
    std::vector<uint8_t> b;

    // Header.
    b.insert(b.end(), { 'E', 'L', 'E', 'M' });
    put_u8(b, 3);                                     // BLOB_VERSION
    put_u8(b, 0x00);                                  // flags: unsynced
    put_u8(b, target_fps);
    put_u8(b, 1);                                     // layer_count
    put_u16(b, 1);                                    // strip_length
    put_u16(b, 1);                                    // buffer_count
    put_u16(b, 1);                                    // pixel_view_count
    put_u16(b, 0);                                    // copy_op_count
    put_f32(b, duration);

    // Buffer sizes: one buffer of 1 pixel.
    put_u16(b, 1);

    // Pixel view 0: identity storage, identity physical, size=1 in buffer 0.
    put_u16(b, 0);                                    // buffer_idx
    put_u16(b, 1);                                    // size
    put_u8(b, 0x01 | 0x02 | 0x04);                    // storage_id | has_phys | phys_id

    // Layer 0: one paint event.
    put_u16(b, 1);                                    // event_count
    put_u8(b, static_cast<uint8_t>(AnimType::Paint));
    put_f32(b, 0.0f);                                 // start
    put_f32(b, duration);                             // duration
    put_u16(b, PIXV_NONE);                            // src
    put_u16(b, 0);                                    // dst
    put_u16(b, PIXV_NONE);                            // work
    // params: u8 mode=Solid, f32 h, f32 s, f32 v, f32 a
    put_u16(b, 1 + 4 * 4);
    put_u8(b, 0);                                     // solid mode
    put_f32(b, 0.0f);                                 // h: red
    put_f32(b, 1.0f);                                 // s
    put_f32(b, 1.0f);                                 // v
    put_f32(b, 1.0f);                                 // a

    return b;
}

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

struct Harness
{
    FakeNetworkInterface net;
    FakeUdpTransport discovery_udp;
    FakeUdpTransport sync_udp;
    FakeUdpTransport log_udp;
    FakeTcpTransport tcp;
    FakeFileStore files;
    MemoryKeyValueStore kv;
    RecordingSystemPlatform system{tcp};
    RecordingFrameOutput output;
    DeviceIdentity identity;
    DiscoveryClient discovery{discovery_udp, identity};
    App app{net, discovery, tcp, sync_udp, log_udp, files, kv, system,
            output, identity};

    int64_t now = 1'000'000;

    explicit Harness(bool with_profile = true)
    {
        std::strcpy(identity.uid, "sim-app");
        identity.boot_token = 0x11223344;
        set_test_now_us(now);
        if (with_profile) {
            REQUIRE(save_hardware_profile(kv, HardwareProfile(1)));
        }
        app.begin();
    }

    void advance(int64_t delta_us)
    {
        now += delta_us;
        set_test_now_us(now);
    }

    void attach()
    {
        discovery_udp.inbox.push_back(make_offer(CONTROLLER_IP, CONTROLLER_PORT));
        app.tick();
        REQUIRE(app.is_attached());
        tcp.sent.clear();
    }

    // Queue one command message and run the tick that serves it.
    void command(uint8_t opcode, const std::vector<uint8_t>& payload = {})
    {
        tcp.inbox = make_msg(opcode, payload);
        app.tick();
    }

    size_t frames() const { return output.frames.size(); }
    const RecordingFrameOutput::Frame& last_frame() const
    {
        REQUIRE(!output.frames.empty());
        return output.frames.back();
    }
};

}  // namespace

// ---------------------------------------------------------------------------
// begin()
// ---------------------------------------------------------------------------

TEST_CASE("begin applies the stored hardware profile")
{
    Harness h;
    CHECK((h.app.status().flags & STATUS_FLAG_PROFILE_PRESENT) != 0);
    CHECK(h.output.profiles_applied == 1);
    CHECK(h.output.profile_strip_length == 1);
}

TEST_CASE("begin without a stored profile leaves the flag clear")
{
    Harness h(/*with_profile=*/false);
    CHECK((h.app.status().flags & STATUS_FLAG_PROFILE_PRESENT) == 0);
    CHECK(h.output.profiles_applied == 0);
}

// ---------------------------------------------------------------------------
// Attach / detach
// ---------------------------------------------------------------------------

TEST_CASE("an OFFER attaches the link and sets the mode")
{
    Harness h;
    CHECK(h.app.status().mode == DeviceMode::DetachedBlank);

    h.discovery_udp.inbox.push_back(make_offer(CONTROLLER_IP, CONTROLLER_PORT));
    h.app.tick();

    CHECK(h.app.is_attached());
    CHECK(h.app.status().mode == DeviceMode::AttachedControlled);
    CHECK_FALSE(h.tcp.sent.empty());  // REGISTER; bytes covered in link tests
}

TEST_CASE("attaching points the sync client at the controller")
{
    Harness h;
    h.app.tick();
    CHECK(h.sync_udp.sent.empty());  // detached: no sync target

    h.attach();
    CHECK_FALSE(h.sync_udp.sent.empty());  // burst PING fired this tick
}

TEST_CASE("link silence detaches, blanks the strip, and flips the mode")
{
    Harness h;
    h.attach();

    h.advance(PING_TIMEOUT_MS * 1000 + 1);
    h.app.tick();

    CHECK_FALSE(h.app.is_attached());
    CHECK(h.app.status().mode == DeviceMode::DetachedBlank);
    REQUIRE(h.frames() == 1);
    CHECK(is_black(h.last_frame()));
}

// ---------------------------------------------------------------------------
// Reboot ordering
// ---------------------------------------------------------------------------

TEST_CASE("a Reboot command reboots only after the ACK is on the wire")
{
    Harness h;
    h.attach();

    h.command(CMD_REBOOT);

    REQUIRE(h.system.reboots == 1);
    CHECK(h.system.sent_at_reboot == ack_ok());
}

TEST_CASE("a reboot ACK the socket resists is drained before rebooting")
{
    Harness h;
    h.attach();

    // The socket refuses the ACK three times (1 ms of fake time each),
    // then opens up: the drain inside the reboot tick must retry until
    // the whole ACK is out, and only then reboot.
    h.tcp.stalled_writes = 3;
    h.tcp.advance_us_on_write = 1'000;
    h.command(CMD_REBOOT);

    REQUIRE(h.system.reboots == 1);
    CHECK(h.system.sent_at_reboot == ack_ok());
}

TEST_CASE("reboot proceeds once the drain deadline passes")
{
    Harness h;
    h.attach();

    // The socket never takes a byte; each retry costs 100 ms of fake
    // time, so the 500 ms drain deadline passes after a few attempts.
    h.tcp.stalled_writes = 1'000'000;
    h.tcp.advance_us_on_write = 100'000;
    h.command(CMD_REBOOT);

    REQUIRE(h.system.reboots == 1);
    CHECK(h.system.sent_at_reboot.empty());  // ACK abandoned, reboot anyway
}

TEST_CASE("SET_PROFILE's reboot also waits for its ACK")
{
    Harness h;
    h.attach();

    h.tcp.stalled_writes = 3;
    h.tcp.advance_us_on_write = 1'000;
    std::vector<uint8_t> new_length;
    put_u16(new_length, 2);   // differs from the stored profile (1)
    h.command(CMD_SET_PROFILE, new_length);

    REQUIRE(h.system.reboots == 1);
    CHECK(h.system.sent_at_reboot == ack_ok());
}

// ---------------------------------------------------------------------------
// Frame pacing
// ---------------------------------------------------------------------------

TEST_CASE("frames are paced by the program's target fps")
{
    Harness h;
    h.attach();

    h.command(CMD_LOAD, build_paint_blob(2.0f, /*target_fps=*/50));
    CHECK(h.frames() == 0);

    std::vector<uint8_t> start;
    put_i64(start, 0);
    h.command(CMD_START, start);

    // First frame renders in the starting tick.
    REQUIRE(h.frames() == 1);
    CHECK(is_red(h.last_frame()));

    // Same instant: the next frame is not due yet.
    h.app.tick();
    CHECK(h.frames() == 1);

    // One 20 ms frame interval later: one more frame.
    h.advance(20'000);
    h.app.tick();
    CHECK(h.frames() == 2);

    // Half an interval: still waiting.
    h.advance(10'000);
    h.app.tick();
    CHECK(h.frames() == 2);

    h.advance(10'000);
    h.app.tick();
    CHECK(h.frames() == 3);
}

TEST_CASE("a live program presents its end frame and does not loop")
{
    Harness h;
    h.attach();

    h.command(CMD_LOAD, build_paint_blob(0.1f));
    std::vector<uint8_t> start;
    put_i64(start, 0);
    h.command(CMD_START, start);
    REQUIRE(h.frames() == 1);

    // Past the end: the Ended transition presents the black end frame.
    h.advance(200'000);
    h.app.tick();
    REQUIRE(h.frames() == 2);
    CHECK(is_black(h.last_frame()));

    // No restart: no further frames.
    h.advance(20'000);
    h.app.tick();
    CHECK(h.frames() == 2);
}

TEST_CASE("a local animation restarts on Ended")
{
    Harness h;
    h.attach();

    std::vector<uint8_t> store_payload = name_slot("anim1");
    const std::vector<uint8_t> blob = build_paint_blob(0.1f);
    store_payload.insert(store_payload.end(), blob.begin(), blob.end());
    h.command(CMD_STORE_ANIMATION, store_payload);
    CHECK(h.frames() == 0);

    std::vector<uint8_t> play;
    put_u16(play, 0);
    h.command(CMD_PLAY_LOCAL_ANIMATION, play);
    REQUIRE(h.frames() == 1);
    CHECK(is_red(h.last_frame()));

    // Past the end: black end frame, then the program restarts.
    h.advance(200'000);
    h.app.tick();
    REQUIRE(h.frames() == 2);
    CHECK(is_black(h.last_frame()));

    // The restarted program renders again.
    h.advance(20'000);
    h.app.tick();
    REQUIRE(h.frames() == 3);
    CHECK(is_red(h.last_frame()));
}

// ---------------------------------------------------------------------------
// Detach-to-background
// ---------------------------------------------------------------------------

namespace {

// Store a long red background animation on an attached device (order index 0).
void install_background(Harness& h, const char* name = "bg")
{
    std::vector<uint8_t> payload = name_slot(name);
    const std::vector<uint8_t> blob = build_paint_blob(10.0f);
    payload.insert(payload.end(), blob.begin(), blob.end());
    h.command(CMD_STORE_ANIMATION, payload);
}

}  // namespace

TEST_CASE("detach resumes the stored background animation")
{
    Harness h;
    h.attach();
    install_background(h);
    h.output.frames.clear();

    h.advance(PING_TIMEOUT_MS * 1000 + 1);
    h.app.tick();  // link silence -> detach

    CHECK_FALSE(h.app.is_attached());
    CHECK(h.app.status().mode == DeviceMode::DetachedBackground);
    REQUIRE(h.frames() >= 1);
    CHECK(is_red(h.last_frame()));  // rendering the background, not blanked
}

TEST_CASE("detach with no stored background still blanks")
{
    // Same as the plain detach test, but stated alongside the background path:
    // an empty store falls back to the blank behaviour.
    Harness h;
    h.attach();

    h.advance(PING_TIMEOUT_MS * 1000 + 1);
    h.app.tick();

    CHECK(h.app.status().mode == DeviceMode::DetachedBlank);
    REQUIRE(h.frames() == 1);
    CHECK(is_black(h.last_frame()));
}

TEST_CASE("re-attaching stops the local background")
{
    Harness h;
    h.attach();
    install_background(h);

    h.advance(PING_TIMEOUT_MS * 1000 + 1);
    h.app.tick();
    REQUIRE(h.app.status().mode == DeviceMode::DetachedBackground);

    h.attach();  // controller comes back
    CHECK(h.app.status().mode == DeviceMode::AttachedControlled);

    // Playback was reset on attach, so the device renders nothing on its own
    // now (the background is not still looping).
    const size_t before = h.frames();
    h.advance(1'000'000);
    h.app.tick();
    CHECK(h.frames() == before);
}

TEST_CASE("boot resumes the stored background from the persisted store")
{
    Harness h;
    h.attach();
    install_background(h);

    // A second device boots over the same persisted store and profile, with no
    // controller present: it must come up playing the background (the reboot
    // path, which begin() now covers).
    FakeNetworkInterface net2;
    FakeUdpTransport discovery2, sync2, log2;
    FakeTcpTransport tcp2;
    RecordingSystemPlatform system2{tcp2};
    RecordingFrameOutput output2;
    DeviceIdentity id2;
    std::strcpy(id2.uid, "sim-app");
    id2.boot_token = 0x55667788;
    DiscoveryClient discovery_client2{discovery2, id2};
    App app2{net2, discovery_client2, tcp2, sync2, log2, h.files, h.kv,
             system2, output2, id2};
    app2.begin();

    CHECK(app2.status().mode == DeviceMode::DetachedBackground);

    set_test_now_us(h.now + 1);
    app2.tick();
    REQUIRE_FALSE(output2.frames.empty());
    CHECK(is_red(output2.frames.back()));
}
