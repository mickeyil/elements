#include <catch2/catch_test_macros.hpp>

#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <vector>

#include "../src/animation_types.h"
#include "../src/playback.h"
#include "../src/runtime_constants.h"
#include "../src/synced_clock.h"
#include "test_platform_clock.h"

namespace {

// ---------------------------------------------------------------------------
// Blob byte builder
// ---------------------------------------------------------------------------

void put_u8(std::vector<uint8_t>& b, uint8_t v) { b.push_back(v); }
void put_u16(std::vector<uint8_t>& b, uint16_t v) {
    b.push_back(v & 0xFF);
    b.push_back((v >> 8) & 0xFF);
}
void put_f32(std::vector<uint8_t>& b, float f) {
    uint8_t bytes[4];
    std::memcpy(bytes, &f, 4);
    b.insert(b.end(), bytes, bytes + 4);
}

// Solid-red paint event covering [0, duration). Strip length = 1.
std::vector<uint8_t> build_paint_blob(float duration, bool requires_sync,
                                      uint8_t target_fps = 50) {
    std::vector<uint8_t> b;

    // Header.
    b.insert(b.end(), { 'E', 'L', 'E', 'M' });
    put_u8(b, 3);                                     // BLOB_VERSION
    put_u8(b, requires_sync ? 0x01 : 0x00);           // flags
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
    const uint16_t params_size = 1 + 4 * 4;
    put_u16(b, params_size);
    put_u8(b, 0);                                     // solid mode
    put_f32(b, 0.0f);                                 // h: red
    put_f32(b, 1.0f);                                 // s
    put_f32(b, 1.0f);                                 // v
    put_f32(b, 1.0f);                                 // a

    return b;
}

// ---------------------------------------------------------------------------
// Test harness
// ---------------------------------------------------------------------------

void set_clock_us(int64_t now_us) {
    set_test_now_us(now_us);
}

bool strip_is_red(const Playback& pb) {
    if (pb.strip().size() == 0) return false;
    const rgb_t& p = pb.strip()[0];
    return p.r == 255 && p.g == 0 && p.b == 0;
}

bool strip_is_black(const Playback& pb) {
    if (pb.strip().size() == 0) return true;
    const rgb_t& p = pb.strip()[0];
    return p.r == 0 && p.g == 0 && p.b == 0;
}

}  // namespace

// ---------------------------------------------------------------------------
// Construction / hardware profile
// ---------------------------------------------------------------------------

TEST_CASE("Playback: default construction has no profile and is IDLE", "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(clock);
    CHECK_FALSE(pb.has_hardware_profile());
    CHECK(pb.state() == DeviceState::IDLE);
    CHECK(pb.duration() == 0.0f);
    CHECK(pb.target_fps() == 0);
    CHECK_FALSE(pb.requires_sync());
}

TEST_CASE("Playback: apply_hardware_profile rejects invalid profile", "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(clock);
    CHECK_FALSE(pb.apply_hardware_profile(HardwareProfile()));
    CHECK_FALSE(pb.has_hardware_profile());
}

TEST_CASE("Playback: apply_hardware_profile resizes the strip and sets the profile",
          "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(clock);
    REQUIRE(pb.apply_hardware_profile(HardwareProfile(8)));
    CHECK(pb.has_hardware_profile());
    CHECK(pb.strip_length() == 8);
    CHECK(pb.state() == DeviceState::IDLE);
}

TEST_CASE("Playback: ctor with strip length applies the profile", "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(4, clock);
    CHECK(pb.has_hardware_profile());
    CHECK(pb.strip_length() == 4);
}

// ---------------------------------------------------------------------------
// handle_load
// ---------------------------------------------------------------------------

TEST_CASE("Playback: handle_load fails without a hardware profile", "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(clock);
    auto blob = build_paint_blob(1.0f, false);
    CHECK_FALSE(pb.handle_load(blob.data(), blob.size()));
    CHECK(pb.state() == DeviceState::IDLE);
}

TEST_CASE("Playback: handle_load decodes a blob and transitions to LOADED", "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(1, clock);
    auto blob = build_paint_blob(2.0f, false, /*target_fps=*/30);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));
    CHECK(pb.state() == DeviceState::LOADED);
    CHECK(pb.duration() == 2.0f);
    CHECK(pb.target_fps() == 30);
    CHECK_FALSE(pb.requires_sync());
}

TEST_CASE("Playback: handle_load on a malformed blob keeps state IDLE", "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(1, clock);
    const uint8_t bad[] = { 'B', 'A', 'D', '!', 3 };
    DecodeError err = DecodeError::Ok;
    CHECK_FALSE(pb.handle_load(bad, sizeof(bad), &err));
    CHECK(err == DecodeError::BadMagic);
    CHECK(pb.state() == DeviceState::IDLE);
    CHECK(pb.duration() == 0.0f);
    CHECK(pb.target_fps() == 0);
}

TEST_CASE("Playback: handle_load forwards StripLengthMismatch", "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(8, clock);  // profile = 8, blob = 1
    auto blob = build_paint_blob(1.0f, false);
    DecodeError err = DecodeError::Ok;
    CHECK_FALSE(pb.handle_load(blob.data(), blob.size(), &err));
    CHECK(err == DecodeError::StripLengthMismatch);
    CHECK(pb.state() == DeviceState::IDLE);
}

TEST_CASE("Playback: handle_load without err_out does not crash", "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(1, clock);
    const uint8_t bad[] = { 'B', 'A', 'D', '!', 3 };
    CHECK_FALSE(pb.handle_load(bad, sizeof(bad)));
    CHECK(pb.state() == DeviceState::IDLE);
}

TEST_CASE("Playback: handle_load reports requires_sync from the blob", "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(1, clock);
    auto blob = build_paint_blob(1.0f, /*requires_sync=*/true);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));
    CHECK(pb.requires_sync());
}

// ---------------------------------------------------------------------------
// handle_start
// ---------------------------------------------------------------------------

TEST_CASE("Playback: handle_start from LOADED transitions to PLAYING", "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(1, clock);
    auto blob = build_paint_blob(1.0f, false);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));
    CHECK(pb.handle_start(0) == PlaybackResult::Ok);
    CHECK(pb.state() == DeviceState::PLAYING);
}

TEST_CASE("Playback: handle_start ignored from IDLE/PLAYING/PAUSED", "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(1, clock);

    CHECK(pb.handle_start(0) == PlaybackResult::WrongState);
    CHECK(pb.state() == DeviceState::IDLE);

    auto blob = build_paint_blob(1.0f, false);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));
    REQUIRE(pb.handle_start(0) == PlaybackResult::Ok);
    REQUIRE(pb.state() == DeviceState::PLAYING);
    CHECK(pb.handle_start(0) == PlaybackResult::WrongState);
    CHECK(pb.state() == DeviceState::PLAYING);

    pb.handle_pause();
    REQUIRE(pb.state() == DeviceState::PAUSED);
    CHECK(pb.handle_start(0) == PlaybackResult::WrongState);
    CHECK(pb.state() == DeviceState::PAUSED);
}

TEST_CASE("Playback: handle_start from ENDED resets engine and replays", "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(1, clock);
    auto blob = build_paint_blob(1.0f, false);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));

    pb.handle_start(0);
    set_clock_us(int64_t(1.0 * 1e6));
    REQUIRE(pb.render_next_frame() == RenderFrameResult::Ended);
    REQUIRE(pb.state() == DeviceState::ENDED);

    set_clock_us(int64_t(1.5 * 1e6));
    CHECK(pb.handle_start(int64_t(1.5 * 1e6)) == PlaybackResult::Ok);
    CHECK(pb.state() == DeviceState::PLAYING);

    REQUIRE(pb.render_next_frame() == RenderFrameResult::Rendered);
    CHECK(strip_is_red(pb));
}

TEST_CASE("Playback: handle_start rejects synced program when not synced",
          "[playback][sync]") {
    set_clock_us(0);
    SyncedClock clock;  // unsynced
    Playback pb(1, clock);
    auto blob = build_paint_blob(1.0f, /*requires_sync=*/true);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));

    CHECK(pb.handle_start(0) == PlaybackResult::Unsynced);
    CHECK(pb.state() == DeviceState::LOADED);
}

TEST_CASE("Playback: handle_start admits synced program when synced",
          "[playback][sync]") {
    set_clock_us(0);
    SyncedClock clock;
    clock.apply_sync_offset(0, 60'000'000);  // synced for 60s
    Playback pb(1, clock);
    auto blob = build_paint_blob(1.0f, /*requires_sync=*/true);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));

    CHECK(pb.handle_start(0) == PlaybackResult::Ok);
    CHECK(pb.state() == DeviceState::PLAYING);
}

// ---------------------------------------------------------------------------
// handle_pause / handle_resume
// ---------------------------------------------------------------------------

TEST_CASE("Playback: handle_pause from PLAYING preserves cursor", "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(1, clock);
    auto blob = build_paint_blob(2.0f, false);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));

    pb.handle_start(0);
    set_clock_us(int64_t(0.5 * 1e6));
    REQUIRE(pb.render_next_frame() == RenderFrameResult::Rendered);

    pb.handle_pause();
    CHECK(pb.state() == DeviceState::PAUSED);
    CHECK(pb.current_t_program() == 0.5f);
}

TEST_CASE("Playback: handle_pause ignored from non-PLAYING", "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(1, clock);

    pb.handle_pause();
    CHECK(pb.state() == DeviceState::IDLE);

    auto blob = build_paint_blob(1.0f, false);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));
    pb.handle_pause();
    CHECK(pb.state() == DeviceState::LOADED);
}

TEST_CASE("Playback: handle_resume from PAUSED transitions to PLAYING", "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(1, clock);
    auto blob = build_paint_blob(2.0f, false);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));

    pb.handle_start(0);
    set_clock_us(int64_t(0.5 * 1e6));
    REQUIRE(pb.render_next_frame() == RenderFrameResult::Rendered);
    pb.handle_pause();
    REQUIRE(pb.state() == DeviceState::PAUSED);

    CHECK(pb.handle_resume(0) == PlaybackResult::Ok);
    CHECK(pb.state() == DeviceState::PLAYING);
}

TEST_CASE("Playback: handle_resume rejected for synced program when not synced",
          "[playback][sync]") {
    set_clock_us(0);
    SyncedClock clock;
    clock.apply_sync_offset(0, 1'000'000);  // synced briefly
    Playback pb(1, clock);
    auto blob = build_paint_blob(2.0f, /*requires_sync=*/true);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));

    pb.handle_start(0);
    pb.handle_pause();
    REQUIRE(pb.state() == DeviceState::PAUSED);

    set_clock_us(int64_t(2 * 1e6));  // lease expired
    REQUIRE_FALSE(clock.is_synced());
    CHECK(pb.handle_resume(0) == PlaybackResult::Unsynced);
    CHECK(pb.state() == DeviceState::PAUSED);
}

TEST_CASE("Playback: handle_resume ignored from non-PAUSED", "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(1, clock);

    CHECK(pb.handle_resume(0) == PlaybackResult::WrongState);
    CHECK(pb.state() == DeviceState::IDLE);

    auto blob = build_paint_blob(1.0f, false);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));
    CHECK(pb.handle_resume(0) == PlaybackResult::WrongState);
    CHECK(pb.state() == DeviceState::LOADED);

    pb.handle_start(0);
    CHECK(pb.handle_resume(0) == PlaybackResult::WrongState);
    CHECK(pb.state() == DeviceState::PLAYING);
}

// ---------------------------------------------------------------------------
// handle_jump
// ---------------------------------------------------------------------------

TEST_CASE("Playback: handle_jump rejects target at or behind cursor", "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(1, clock);
    auto blob = build_paint_blob(2.0f, false);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));

    // From LOADED, cursor=0. Jump to 0 is rejected (not strictly ahead).
    CHECK(pb.handle_jump(0.0f) == PlaybackResult::BadTime);
    CHECK(pb.state() == DeviceState::LOADED);
    CHECK(pb.current_t_program() == 0.0f);

    // Move cursor to 0.5 via a real jump.
    REQUIRE(pb.handle_jump(0.5f) == PlaybackResult::Ok);
    REQUIRE(pb.state() == DeviceState::PAUSED);
    REQUIRE(pb.current_t_program() == 0.5f);

    // Now reject jumps behind or at 0.5.
    CHECK(pb.handle_jump(0.5f) == PlaybackResult::BadTime);
    CHECK(pb.handle_jump(0.25f) == PlaybackResult::BadTime);
    CHECK(pb.current_t_program() == 0.5f);
}

TEST_CASE("Playback: handle_jump rejects out-of-range target", "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(1, clock);
    auto blob = build_paint_blob(1.0f, false);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));

    CHECK(pb.handle_jump(-0.1f) == PlaybackResult::BadTime);
    CHECK(pb.handle_jump(1.0f) == PlaybackResult::BadTime);   // exclusive upper
    CHECK(pb.handle_jump(2.0f) == PlaybackResult::BadTime);
    CHECK(pb.state() == DeviceState::LOADED);
    CHECK(pb.current_t_program() == 0.0f);
}

TEST_CASE("Playback: handle_jump rejects non-finite target", "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(1, clock);
    auto blob = build_paint_blob(1.0f, false);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));

    const float nan = std::numeric_limits<float>::quiet_NaN();
    const float pos_inf = std::numeric_limits<float>::infinity();
    const float neg_inf = -std::numeric_limits<float>::infinity();

    CHECK(pb.handle_jump(nan) == PlaybackResult::BadTime);
    CHECK(pb.handle_jump(pos_inf) == PlaybackResult::BadTime);
    CHECK(pb.handle_jump(neg_inf) == PlaybackResult::BadTime);
    CHECK(pb.state() == DeviceState::LOADED);
    CHECK(pb.current_t_program() == 0.0f);
}

TEST_CASE("Playback: handle_jump from LOADED advances cursor and pauses",
          "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(1, clock);
    auto blob = build_paint_blob(2.0f, false);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));

    REQUIRE(pb.handle_jump(0.5f) == PlaybackResult::Ok);
    CHECK(pb.state() == DeviceState::PAUSED);
    CHECK(pb.current_t_program() == 0.5f);
    CHECK(strip_is_black(pb));
}

TEST_CASE("Playback: handle_jump rejected from PLAYING/IDLE/ENDED", "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(1, clock);

    // IDLE
    CHECK(pb.handle_jump(0.5f) == PlaybackResult::WrongState);
    CHECK(pb.state() == DeviceState::IDLE);

    auto blob = build_paint_blob(1.0f, false);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));

    // PLAYING
    pb.handle_start(0);
    REQUIRE(pb.state() == DeviceState::PLAYING);
    CHECK(pb.handle_jump(0.5f) == PlaybackResult::WrongState);
    CHECK(pb.state() == DeviceState::PLAYING);

    // ENDED
    set_clock_us(int64_t(1.0 * 1e6));
    REQUIRE(pb.render_next_frame() == RenderFrameResult::Ended);
    REQUIRE(pb.state() == DeviceState::ENDED);
    CHECK(pb.handle_jump(0.5f) == PlaybackResult::WrongState);
    CHECK(pb.state() == DeviceState::ENDED);
}

TEST_CASE("Playback: handle_jump rejects synced program when not synced",
          "[playback][sync]") {
    set_clock_us(0);
    SyncedClock clock;  // unsynced
    Playback pb(1, clock);
    auto blob = build_paint_blob(2.0f, /*requires_sync=*/true);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));

    CHECK(pb.handle_jump(0.5f) == PlaybackResult::Unsynced);
    CHECK(pb.state() == DeviceState::LOADED);
    CHECK(pb.current_t_program() == 0.0f);
}

// ---------------------------------------------------------------------------
// handle_stop
// ---------------------------------------------------------------------------

TEST_CASE("Playback: handle_stop from non-IDLE clears strip and goes to LOADED",
          "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(1, clock);
    auto blob = build_paint_blob(2.0f, false);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));

    pb.handle_start(0);
    set_clock_us(int64_t(0.5 * 1e6));
    REQUIRE(pb.render_next_frame() == RenderFrameResult::Rendered);
    REQUIRE(strip_is_red(pb));

    REQUIRE(pb.handle_stop() == RenderFrameResult::Rendered);
    CHECK(pb.state() == DeviceState::LOADED);
    CHECK(pb.current_t_program() == 0.0f);
    CHECK(strip_is_black(pb));
}

TEST_CASE("Playback: handle_stop from IDLE is a no-op", "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(clock);
    CHECK(pb.handle_stop() == RenderFrameResult::Unchanged);
    CHECK(pb.state() == DeviceState::IDLE);
}

// ---------------------------------------------------------------------------
// render_next_frame
// ---------------------------------------------------------------------------

TEST_CASE("Playback: render_next_frame returns Unchanged outside PLAYING",
          "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(1, clock);
    CHECK(pb.render_next_frame() == RenderFrameResult::Unchanged);

    auto blob = build_paint_blob(1.0f, false);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));
    CHECK(pb.render_next_frame() == RenderFrameResult::Unchanged);
}

TEST_CASE("Playback: render_next_frame waits for clock to reach program_start_us",
          "[playback][sync]") {
    set_clock_us(0);
    SyncedClock clock;
    clock.apply_sync_offset(0, 60'000'000);   // synced, local == remote
    REQUIRE(clock.is_synced());

    Playback pb(1, clock);
    auto blob = build_paint_blob(1.0f, /*requires_sync=*/true);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));

    // Schedule start at remote=1s.
    pb.handle_start(int64_t(1.0 * 1e6));
    CHECK(pb.state() == DeviceState::PLAYING);

    set_clock_us(int64_t(0.5 * 1e6));   // remote=0.5s -> wait
    CHECK(pb.render_next_frame() == RenderFrameResult::Unchanged);
    CHECK(strip_is_black(pb));

    set_clock_us(int64_t(1.0 * 1e6));   // remote=1.0s -> render
    CHECK(pb.render_next_frame() == RenderFrameResult::Rendered);
    CHECK(strip_is_red(pb));
}

TEST_CASE("Playback: render_next_frame transitions PLAYING -> ENDED at duration",
          "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(1, clock);
    auto blob = build_paint_blob(1.0f, false);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));

    pb.handle_start(0);
    set_clock_us(int64_t(1.0 * 1e6));   // exclusive upper bound on render
    CHECK(pb.render_next_frame() == RenderFrameResult::Ended);
    CHECK(pb.state() == DeviceState::ENDED);
    CHECK(strip_is_black(pb));
    CHECK(pb.current_t_program() == 1.0f);
}

TEST_CASE("Playback: render_next_frame returns Ended exactly once", "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(1, clock);
    auto blob = build_paint_blob(0.5f, false);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));

    pb.handle_start(0);
    set_clock_us(int64_t(0.5 * 1e6));
    REQUIRE(pb.render_next_frame() == RenderFrameResult::Ended);

    set_clock_us(int64_t(1.0 * 1e6));
    CHECK(pb.render_next_frame() == RenderFrameResult::Unchanged);
    CHECK(pb.state() == DeviceState::ENDED);
}

TEST_CASE("Playback: render_next_frame waits while cursor leads clock (post-JUMP)",
          "[playback][sync]") {
    set_clock_us(0);
    SyncedClock clock;
    clock.apply_sync_offset(0, 60'000'000);
    REQUIRE(clock.is_synced());

    Playback pb(1, clock);
    auto blob = build_paint_blob(2.0f, /*requires_sync=*/true);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));

    REQUIRE(pb.handle_jump(0.5f) == PlaybackResult::Ok);
    REQUIRE(pb.state() == DeviceState::PAUSED);
    REQUIRE(pb.current_t_program() == 0.5f);

    pb.handle_resume(0);   // synced: remote-clock anchor
    REQUIRE(pb.state() == DeviceState::PLAYING);

    // remote=0.25s, cursor at 0.5s -> wait.
    set_clock_us(int64_t(0.25 * 1e6));
    CHECK(pb.render_next_frame() == RenderFrameResult::Unchanged);
    CHECK(strip_is_black(pb));

    // remote=0.6s -> cursor catches up.
    set_clock_us(int64_t(0.6 * 1e6));
    CHECK(pb.render_next_frame() == RenderFrameResult::Rendered);
    CHECK(strip_is_red(pb));
}

TEST_CASE("Playback: synced program reads remote clock domain",
          "[playback][sync]") {
    set_clock_us(0);
    SyncedClock clock;
    // offset_us = local - remote = 1_000_000 (local leads remote by 1s).
    clock.apply_sync_offset(1'000'000, 60'000'000);
    REQUIRE(clock.is_synced());

    Playback pb(1, clock);
    auto blob = build_paint_blob(1.0f, /*requires_sync=*/true);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));

    // Owner anchors the program at remote=0us.
    pb.handle_start(0);

    // Local=0 -> remote=-1_000_000 -> t_program=-1s -> Unchanged.
    set_clock_us(0);
    CHECK(pb.render_next_frame() == RenderFrameResult::Unchanged);

    // Local=1.5e6 -> remote=0.5e6 -> t_program=0.5s -> render.
    set_clock_us(int64_t(1.5 * 1e6));
    CHECK(pb.render_next_frame() == RenderFrameResult::Rendered);
    CHECK(strip_is_red(pb));
}

TEST_CASE("Playback: unsynced program reads local clock domain", "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    clock.apply_sync_offset(1'000'000, 60'000'000);  // remote != local

    Playback pb(1, clock);
    auto blob = build_paint_blob(1.0f, /*requires_sync=*/false);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));

    pb.handle_start(0);
    set_clock_us(int64_t(0.5 * 1e6));
    CHECK(pb.render_next_frame() == RenderFrameResult::Rendered);
    CHECK(strip_is_red(pb));
}

TEST_CASE("Playback: unsynced handle_start ignores caller anchor, starts now",
          "[playback]") {
    set_clock_us(int64_t(7.0 * 1e6));    // device local clock at 7s
    SyncedClock clock;                   // unsynced
    Playback pb(1, clock);
    auto blob = build_paint_blob(2.0f, /*requires_sync=*/false);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));

    pb.handle_start(/*bogus*/ int64_t(99.0 * 1e6));
    REQUIRE(pb.state() == DeviceState::PLAYING);

    // 0.5s of wall time later, t_program should be 0.5s.
    set_clock_us(int64_t(7.5 * 1e6));
    CHECK(pb.render_next_frame() == RenderFrameResult::Rendered);
    CHECK(pb.current_t_program() == 0.5f);
}

TEST_CASE("Playback: unsynced handle_resume ignores anchor, resumes from cursor immediately",
          "[playback]") {
    set_clock_us(int64_t(3.0 * 1e6));
    SyncedClock clock;                   // unsynced
    Playback pb(1, clock);
    auto blob = build_paint_blob(2.0f, /*requires_sync=*/false);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));

    REQUIRE(pb.handle_jump(0.5f) == PlaybackResult::Ok);
    REQUIRE(pb.current_t_program() == 0.5f);

    // Resume with bogus anchor. Unsynced should resume immediately from 0.5s.
    set_clock_us(int64_t(3.2 * 1e6));    // 0.2s of wall time has passed
    pb.handle_resume(/*bogus*/ int64_t(99.0 * 1e6));
    REQUIRE(pb.state() == DeviceState::PLAYING);

    // No wall-time wait: render right now should produce a frame at 0.5s.
    CHECK(pb.render_next_frame() == RenderFrameResult::Rendered);
    CHECK(pb.current_t_program() == 0.5f);
}

// ---------------------------------------------------------------------------
// render_black_frame / reset_for_detach
// ---------------------------------------------------------------------------

TEST_CASE("Playback: render_black_frame clears strip and returns Rendered",
          "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(1, clock);
    auto blob = build_paint_blob(1.0f, false);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));

    pb.handle_start(0);
    set_clock_us(int64_t(0.5 * 1e6));
    REQUIRE(pb.render_next_frame() == RenderFrameResult::Rendered);
    REQUIRE(strip_is_red(pb));

    CHECK(pb.render_black_frame() == RenderFrameResult::Rendered);
    CHECK(strip_is_black(pb));
    // State unaffected.
    CHECK(pb.state() == DeviceState::PLAYING);
}

TEST_CASE("Playback: reset_for_detach drops program and clears strip",
          "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(1, clock);
    auto blob = build_paint_blob(1.0f, false);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));
    pb.handle_start(0);
    set_clock_us(int64_t(0.5 * 1e6));
    REQUIRE(pb.render_next_frame() == RenderFrameResult::Rendered);

    pb.reset_for_detach();
    CHECK(pb.state() == DeviceState::IDLE);
    CHECK(pb.duration() == 0.0f);
    CHECK(pb.target_fps() == 0);
    CHECK_FALSE(pb.requires_sync());
    CHECK(strip_is_black(pb));
    // Profile is preserved.
    CHECK(pb.has_hardware_profile());
    CHECK(pb.strip_length() == 1);
}

TEST_CASE("Playback: apply_hardware_profile resets program state", "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(1, clock);
    auto blob = build_paint_blob(1.0f, false);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));
    REQUIRE(pb.state() == DeviceState::LOADED);

    REQUIRE(pb.apply_hardware_profile(HardwareProfile(2)));
    CHECK(pb.state() == DeviceState::IDLE);
    CHECK(pb.duration() == 0.0f);
    CHECK(pb.strip_length() == 2);
}

// ---------------------------------------------------------------------------
// current_t_program reporting
// ---------------------------------------------------------------------------

TEST_CASE("Playback: current_t_program reports 0 in IDLE/LOADED and duration in ENDED",
          "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(1, clock);
    CHECK(pb.current_t_program() == 0.0f);

    auto blob = build_paint_blob(1.0f, false);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));
    CHECK(pb.current_t_program() == 0.0f);

    pb.handle_start(0);
    set_clock_us(int64_t(1.0 * 1e6));
    REQUIRE(pb.render_next_frame() == RenderFrameResult::Ended);
    CHECK(pb.current_t_program() == 1.0f);
}

TEST_CASE("Playback: current_t_program follows the cursor in PLAYING", "[playback]") {
    set_clock_us(0);
    SyncedClock clock;
    Playback pb(1, clock);
    auto blob = build_paint_blob(2.0f, false);
    REQUIRE(pb.handle_load(blob.data(), blob.size()));
    pb.handle_start(0);

    set_clock_us(int64_t(0.25 * 1e6));
    REQUIRE(pb.render_next_frame() == RenderFrameResult::Rendered);
    CHECK(pb.current_t_program() == 0.25f);

    set_clock_us(int64_t(0.75 * 1e6));
    REQUIRE(pb.render_next_frame() == RenderFrameResult::Rendered);
    CHECK(pb.current_t_program() == 0.75f);
}
