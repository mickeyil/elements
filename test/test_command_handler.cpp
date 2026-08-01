#include <catch2/catch_test_macros.hpp>

#include <cstring>
#include <limits>
#include <map>
#include <string>
#include <vector>

#include "controller/animation_store.h"
#include "core/animation_types.h"
#include "controller/app_context.h"
#include "controller/command_handler.h"
#include "controller/crc32.h"
#include "controller/device_status.h"
#include "controller/hardware_profile_store.h"
#include "controller/link_protocol.h"
#include "controller/playback.h"
#include "core/runtime_constants.h"
#include "platform/system_platform.h"
#include "test_platform_clock.h"
#include "controller/wire_writer.h"

namespace {

// ---------------------------------------------------------------------------
// Platform fakes (file storage, key-value storage, system)
// ---------------------------------------------------------------------------

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

    bool has_key(const char* key) override
    {
        return _u8.count(key) || _u16.count(key) || _f32.count(key) ||
               _str.count(key);
    }

    bool remove(const char* key) override
    {
        return _u8.erase(key) + _u16.erase(key) + _f32.erase(key) +
               _str.erase(key) > 0;
    }

    bool get_u8(const char* key, uint8_t& out) override { return get_(_u8, key, out); }
    bool get_u16(const char* key, uint16_t& out) override { return get_(_u16, key, out); }
    bool get_f32(const char* key, float& out) override { return get_(_f32, key, out); }

    bool put_u8(const char* key, uint8_t value) override { _u8[key] = value; return true; }
    bool put_u16(const char* key, uint16_t value) override { _u16[key] = value; return true; }
    bool put_f32(const char* key, float value) override { _f32[key] = value; return true; }

    bool put_str(const char* key, const char* value) override
    {
        _str[key] = value;
        return true;
    }

    int get_str(const char* key, char* out, size_t out_cap) override
    {
        auto it = _str.find(key);
        if (it == _str.end() || it->second.size() + 1 > out_cap) return -1;
        std::memcpy(out, it->second.c_str(), it->second.size() + 1);
        return static_cast<int>(it->second.size());
    }

private:
    template <typename T>
    static bool get_(const std::map<std::string, T>& m, const char* key, T& out)
    {
        auto it = m.find(key);
        if (it == m.end()) return false;
        out = it->second;
        return true;
    }

    std::map<std::string, uint8_t>  _u8;
    std::map<std::string, uint16_t> _u16;
    std::map<std::string, float>    _f32;
    std::map<std::string, std::string> _str;
};

class FakeSystemPlatform : public SystemPlatform
{
public:
    void reboot() override { ++reboot_calls; }
    int reboot_calls = 0;
};

// ---------------------------------------------------------------------------
// Payload / blob builders
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
    for (int i = 0; i < 8; ++i) b.push_back(uint8_t(uint64_t(v) >> (8 * i)));
}
void put_f32(std::vector<uint8_t>& b, float f)
{
    uint8_t bytes[4];
    std::memcpy(bytes, &f, 4);
    b.insert(b.end(), bytes, bytes + 4);
}
void put_name_slot(std::vector<uint8_t>& b, const char* name)
{
    uint8_t slot[ANIM_NAME_SIZE] = {};
    std::memcpy(slot, name, std::strlen(name));
    b.insert(b.end(), slot, slot + sizeof(slot));
}

// Solid-red paint program covering [0, duration); decodes for the
// given strip length. Same shape as test_playback's builder.
std::vector<uint8_t> build_paint_blob(uint16_t strip_length, float duration,
                                      bool requires_sync = false)
{
    std::vector<uint8_t> b;
    b.insert(b.end(), { 'E', 'L', 'E', 'M' });
    put_u8(b, 3);                                     // BLOB_VERSION
    put_u8(b, requires_sync ? 0x01 : 0x00);           // flags
    put_u8(b, 50);                                    // target_fps
    put_u8(b, 1);                                     // layer_count
    put_u16(b, strip_length);
    put_u16(b, 1);                                    // buffer_count
    put_u16(b, 1);                                    // pixel_view_count
    put_u16(b, 0);                                    // copy_op_count
    put_f32(b, duration);

    put_u16(b, strip_length);                         // buffer 0 size

    put_u16(b, 0);                                    // view 0: buffer_idx
    put_u16(b, strip_length);                         // size
    put_u8(b, 0x01 | 0x02 | 0x04);                    // storage_id | has_phys | phys_id

    put_u16(b, 1);                                    // layer 0: event_count
    put_u8(b, static_cast<uint8_t>(AnimType::Paint));
    put_f32(b, 0.0f);                                 // start
    put_f32(b, duration);
    put_u16(b, PIXV_NONE);                            // src
    put_u16(b, 0);                                    // dst
    put_u16(b, PIXV_NONE);                            // work
    put_u16(b, 1 + 4 * 4);                            // params_size
    put_u8(b, 0);                                     // solid mode
    put_f32(b, 0.0f);                                 // h
    put_f32(b, 1.0f);                                 // s
    put_f32(b, 1.0f);                                 // v
    put_f32(b, 1.0f);                                 // a
    return b;
}

// ---------------------------------------------------------------------------
// Harness: real Playback (strip length 1) and AnimationStore over fakes
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

    uint8_t reply[1024] = {};
    size_t  reply_len = 0;

    Harness() { set_test_now_us(0); }

    AckStatus run(uint8_t opcode, const std::vector<uint8_t>& payload = {})
    {
        WireWriter w(reply, sizeof(reply));
        const AckStatus status_out =
            handler.handle(opcode, payload.data(), payload.size(), w);
        reply_len = w.bytes_written();
        return status_out;
    }

    AckStatus load_live(uint16_t strip_length = 1, float duration = 2.0f)
    {
        return run(CMD_LOAD, build_paint_blob(strip_length, duration));
    }

    AckStatus store_anim(const char* name, const std::vector<uint8_t>& blob)
    {
        std::vector<uint8_t> p;
        put_name_slot(p, name);
        p.insert(p.end(), blob.begin(), blob.end());
        return run(CMD_STORE_ANIMATION, p);
    }
};

}  // namespace

// ---------------------------------------------------------------------------
// Dispatch
// ---------------------------------------------------------------------------

TEST_CASE("unknown opcodes ACK UnknownCommand")
{
    Harness h;
    CHECK(h.run(0x00) == AckStatus::UnknownCommand);  // inbound REGISTER
    CHECK(h.run(0x02) == AckStatus::UnknownCommand);  // reserved
    CHECK(h.run(0x7F) == AckStatus::UnknownCommand);
    CHECK(h.run(0x80) == AckStatus::UnknownCommand);  // stray ACK
}

TEST_CASE("ping ACKs Ok with no payload; trailing bytes are rejected")
{
    Harness h;
    CHECK(h.run(CMD_PING) == AckStatus::Ok);
    CHECK(h.reply_len == 0);
    CHECK(h.run(CMD_PING, {0x01}) == AckStatus::BadPayload);
}

TEST_CASE("reboot sets the flag without calling SystemPlatform")
{
    Harness h;
    CHECK(h.run(CMD_REBOOT) == AckStatus::Ok);
    CHECK(h.ctx.reboot_requested);
    CHECK(h.system.reboot_calls == 0);
}

// ---------------------------------------------------------------------------
// Load / playback commands
// ---------------------------------------------------------------------------

TEST_CASE("load decodes a valid blob and reaches LOADED")
{
    Harness h;
    CHECK(h.load_live() == AckStatus::Ok);
    CHECK(h.playback.state() == DeviceState::LOADED);
}

TEST_CASE("load failures map onto the ACK statuses")
{
    Harness h;
    CHECK(h.run(CMD_LOAD) == AckStatus::BadPayload);              // empty
    CHECK(h.run(CMD_LOAD, {1, 2, 3}) == AckStatus::Error);        // garbage
    CHECK(h.load_live(2) == AckStatus::ProfileMismatch);          // wrong strip
    CHECK(h.playback.state() == DeviceState::IDLE);
}

TEST_CASE("start/pause/resume/stop drive playback and ACK truthfully")
{
    Harness h;

    std::vector<uint8_t> start0;
    put_i64(start0, 0);

    CHECK(h.run(CMD_START, start0) == AckStatus::WrongState);  // nothing loaded
    REQUIRE(h.load_live() == AckStatus::Ok);
    CHECK(h.run(CMD_RESUME, start0) == AckStatus::WrongState); // not paused

    CHECK(h.run(CMD_START, start0) == AckStatus::Ok);
    CHECK(h.playback.state() == DeviceState::PLAYING);

    CHECK(h.run(CMD_PAUSE) == AckStatus::Ok);
    CHECK(h.playback.state() == DeviceState::PAUSED);

    CHECK(h.run(CMD_RESUME, start0) == AckStatus::Ok);
    CHECK(h.playback.state() == DeviceState::PLAYING);

    CHECK(h.run(CMD_STOP) == AckStatus::Ok);
    CHECK(h.playback.state() == DeviceState::LOADED);

    CHECK(h.run(CMD_START, {1, 2}) == AckStatus::BadPayload);  // short payload
}

TEST_CASE("synced program without a clock lease ACKs Unsynced on start")
{
    Harness h;
    REQUIRE(h.run(CMD_LOAD, build_paint_blob(1, 2.0f, /*requires_sync=*/true))
            == AckStatus::Ok);

    std::vector<uint8_t> start0;
    put_i64(start0, 0);
    CHECK(h.run(CMD_START, start0) == AckStatus::Unsynced);
}

TEST_CASE("jump maps BadTime onto BadPayload and seeks on success")
{
    Harness h;
    REQUIRE(h.load_live() == AckStatus::Ok);

    std::vector<uint8_t> nan_target;
    put_f32(nan_target, std::numeric_limits<float>::quiet_NaN());
    CHECK(h.run(CMD_JUMP, nan_target) == AckStatus::BadPayload);

    std::vector<uint8_t> behind;
    put_f32(behind, 0.0f);  // not strictly ahead of cursor 0
    CHECK(h.run(CMD_JUMP, behind) == AckStatus::BadPayload);

    std::vector<uint8_t> ahead;
    put_f32(ahead, 0.5f);
    CHECK(h.run(CMD_JUMP, ahead) == AckStatus::Ok);
    CHECK(h.playback.state() == DeviceState::PAUSED);
    CHECK(h.playback.current_t_program() == 0.5f);
}

// ---------------------------------------------------------------------------
// SetProfile
// ---------------------------------------------------------------------------

TEST_CASE("set_profile with the active strip length is a no-op")
{
    Harness h;
    std::vector<uint8_t> p;
    put_u16(p, 1);  // harness playback profile is strip length 1
    CHECK(h.run(CMD_SET_PROFILE, p) == AckStatus::Ok);
    CHECK_FALSE(h.ctx.reboot_requested);

    HardwareProfile stored;
    CHECK_FALSE(load_hardware_profile(h.kv, stored));  // nothing persisted
}

TEST_CASE("set_profile persists a new strip length and requests reboot")
{
    Harness h;
    std::vector<uint8_t> p;
    put_u16(p, 60);
    CHECK(h.run(CMD_SET_PROFILE, p) == AckStatus::Ok);
    CHECK(h.ctx.reboot_requested);

    HardwareProfile stored;
    REQUIRE(load_hardware_profile(h.kv, stored));
    CHECK(stored.strip_length == 60);
}

TEST_CASE("set_profile keeps the stored color order and gamma")
{
    Harness h;
    REQUIRE(save_hardware_profile(
        h.kv, HardwareProfile(1, ColorOrder::BGR, 2.0f)));

    std::vector<uint8_t> p;
    put_u16(p, 60);
    REQUIRE(h.run(CMD_SET_PROFILE, p) == AckStatus::Ok);

    HardwareProfile stored;
    REQUIRE(load_hardware_profile(h.kv, stored));
    CHECK(stored.strip_length == 60);
    CHECK(stored.color_order == ColorOrder::BGR);
    CHECK(stored.gamma == 2.0f);
}

TEST_CASE("set_profile rejects invalid lengths and payloads")
{
    Harness h;
    std::vector<uint8_t> zero;
    put_u16(zero, 0);
    CHECK(h.run(CMD_SET_PROFILE, zero) == AckStatus::BadPayload);

    std::vector<uint8_t> over;
    put_u16(over, MAX_STRIP_PIXELS + 1);
    CHECK(h.run(CMD_SET_PROFILE, over) == AckStatus::BadPayload);

    CHECK(h.run(CMD_SET_PROFILE, {0x01}) == AckStatus::BadPayload);  // short
    CHECK_FALSE(h.ctx.reboot_requested);
}

// ---------------------------------------------------------------------------
// Animation store commands
// ---------------------------------------------------------------------------

TEST_CASE("store_animation stores a blob; bad blobs and short payloads rejected")
{
    Harness h;
    CHECK(h.store_anim("glow", build_paint_blob(1, 1.0f)) == AckStatus::Ok);
    CHECK(h.animations.count() == 1);

    // Synced blobs are live-only; the store rejects them.
    CHECK(h.store_anim("synced", build_paint_blob(1, 1.0f, true))
          == AckStatus::Error);

    // Payload shorter than the 32-byte name slot.
    CHECK(h.run(CMD_STORE_ANIMATION, {1, 2, 3}) == AckStatus::BadPayload);
    CHECK(h.animations.count() == 1);
}

TEST_CASE("erase_animation removes by name; unknown names ACK Error")
{
    Harness h;
    REQUIRE(h.store_anim("glow", build_paint_blob(1, 1.0f)) == AckStatus::Ok);

    std::vector<uint8_t> unknown;
    put_name_slot(unknown, "never-stored");
    CHECK(h.run(CMD_ERASE_ANIMATION, unknown) == AckStatus::Error);

    std::vector<uint8_t> glow;
    put_name_slot(glow, "glow");
    CHECK(h.run(CMD_ERASE_ANIMATION, glow) == AckStatus::Ok);
    CHECK(h.animations.count() == 0);
}

TEST_CASE("set_animation_order applies a permutation and rejects the rest")
{
    Harness h;
    REQUIRE(h.store_anim("a", build_paint_blob(1, 1.0f)) == AckStatus::Ok);
    REQUIRE(h.store_anim("b", build_paint_blob(1, 1.5f)) == AckStatus::Ok);

    std::vector<uint8_t> swap;
    put_u16(swap, 2);
    put_name_slot(swap, "b");
    put_name_slot(swap, "a");
    CHECK(h.run(CMD_SET_ANIMATION_ORDER, swap) == AckStatus::Ok);
    CHECK(std::strcmp(h.animations.entry(0).name, "b") == 0);

    std::vector<uint8_t> not_permutation;
    put_u16(not_permutation, 1);
    put_name_slot(not_permutation, "a");
    CHECK(h.run(CMD_SET_ANIMATION_ORDER, not_permutation) == AckStatus::Error);

    std::vector<uint8_t> over_cap;
    put_u16(over_cap, MAX_STORED_ANIMATIONS + 1);
    CHECK(h.run(CMD_SET_ANIMATION_ORDER, over_cap) == AckStatus::BadPayload);

    std::vector<uint8_t> truncated;
    put_u16(truncated, 2);
    put_name_slot(truncated, "b");
    CHECK(h.run(CMD_SET_ANIMATION_ORDER, truncated) == AckStatus::BadPayload);
}

// ---------------------------------------------------------------------------
// PlayLocalAnimation
// ---------------------------------------------------------------------------

TEST_CASE("play_local_animation loads, starts, and marks the local flag")
{
    Harness h;
    REQUIRE(h.store_anim("glow", build_paint_blob(1, 1.0f)) == AckStatus::Ok);

    std::vector<uint8_t> index0;
    put_u16(index0, 0);
    CHECK(h.run(CMD_PLAY_LOCAL_ANIMATION, index0) == AckStatus::Ok);
    CHECK(h.playback.state() == DeviceState::PLAYING);
    CHECK(h.ctx.local_program_loaded);

    // A live LOAD takes over and clears the local marker.
    REQUIRE(h.load_live() == AckStatus::Ok);
    CHECK_FALSE(h.ctx.local_program_loaded);
}

TEST_CASE("play_local_animation rejects bad indices and mismatched blobs")
{
    Harness h;
    std::vector<uint8_t> index0;
    put_u16(index0, 0);
    CHECK(h.run(CMD_PLAY_LOCAL_ANIMATION, index0) == AckStatus::BadPayload);

    // Stored for strip length 2; harness profile is 1.
    REQUIRE(h.store_anim("wide", build_paint_blob(2, 1.0f)) == AckStatus::Ok);
    CHECK(h.run(CMD_PLAY_LOCAL_ANIMATION, index0) == AckStatus::ProfileMismatch);
    CHECK_FALSE(h.ctx.local_program_loaded);
}

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

TEST_CASE("query_device_status reports mode, flags, and animation count")
{
    Harness h;
    h.status.mode = DeviceMode::DetachedBackground;
    h.status.flags = 0x01;
    REQUIRE(h.store_anim("glow", build_paint_blob(1, 1.0f)) == AckStatus::Ok);

    REQUIRE(h.run(CMD_QUERY_DEVICE_STATUS) == AckStatus::Ok);
    REQUIRE(h.reply_len == 4);
    CHECK(h.reply[0] == MODE_DETACHED_BACKGROUND);
    CHECK(h.reply[1] == 0x01);
    CHECK(h.reply[2] == 1);  // animation_count u16 LE
    CHECK(h.reply[3] == 0);
}

TEST_CASE("query_local_animations lists records in play order")
{
    Harness h;
    const auto blob_a = build_paint_blob(1, 1.0f);
    const auto blob_b = build_paint_blob(2, 1.5f);
    REQUIRE(h.store_anim("a", blob_a) == AckStatus::Ok);
    REQUIRE(h.store_anim("b", blob_b) == AckStatus::Ok);

    REQUIRE(h.run(CMD_QUERY_LOCAL_ANIMATIONS) == AckStatus::Ok);
    REQUIRE(h.reply_len == 2 + 2 * (ANIM_NAME_SIZE + 2 + 4));

    std::vector<uint8_t> expected;
    put_u16(expected, 2);
    put_name_slot(expected, "a");
    put_u16(expected, 1);
    put_u32(expected, crc32_ieee(blob_a.data(), blob_a.size()));
    put_name_slot(expected, "b");
    put_u16(expected, 2);
    put_u32(expected, crc32_ieee(blob_b.data(), blob_b.size()));

    CHECK(std::memcmp(h.reply, expected.data(), expected.size()) == 0);
}

TEST_CASE("queries reject trailing payload bytes")
{
    Harness h;
    CHECK(h.run(CMD_QUERY_DEVICE_STATUS, {0x00}) == AckStatus::BadPayload);
    CHECK(h.run(CMD_QUERY_LOCAL_ANIMATIONS, {0x00}) == AckStatus::BadPayload);
}
