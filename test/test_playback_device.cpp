#include <catch2/catch_test_macros.hpp>
#include <catch2/catch_approx.hpp>
#include "../src/playback_device.h"

#include <cstdio>
#include <cstring>
#include <vector>

// ---------------------------------------------------------------------------
// TestDevice — concrete subclass with controllable clock
// ---------------------------------------------------------------------------

struct TelemetryEntry {
    DeviceState state;
    float t;
    std::string err;
};

class TestDevice : public PlaybackDevice {
public:
    TestDevice(uint16_t len)
        : PlaybackDevice(len, /*gamma_enabled=*/false) {}

    void set_time(int64_t us) { _now = us; }
    int64_t now_mono() override { return _now; }

    int output_frame_count = 0;
    void output_frame() override { output_frame_count++; }

    std::vector<TelemetryEntry> telemetry;
    void send_telemetry(DeviceState s, float t, const char* err = nullptr) override {
        telemetry.push_back({s, t, err ? err : ""});
    }

    const TelemetryEntry& last_telemetry() const { return telemetry.back(); }

private:
    int64_t _now = 0;
};

// ---------------------------------------------------------------------------
// Load test blob from file
// ---------------------------------------------------------------------------

static std::vector<uint8_t> load_blob(const char* path) {
    FILE* f = fopen(path, "rb");
    if (!f) {
        FAIL("Could not open " << path);
        return {};
    }
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    std::vector<uint8_t> data(sz);
    fread(data.data(), 1, sz, f);
    fclose(f);
    return data;
}

static const char* SHIFT_FIXTURE = "test/fixtures/test_shift.bin";

// Helper: check that pixel i has R=G=B=expected
static void check_pixel(const uint8_t* rgb, int i, uint8_t expected) {
    INFO("pixel " << i);
    CHECK(rgb[i * 3 + 0] == expected);
    CHECK(rgb[i * 3 + 1] == expected);
    CHECK(rgb[i * 3 + 2] == expected);
}

// ---------------------------------------------------------------------------
// State machine basics
// ---------------------------------------------------------------------------

TEST_CASE("State machine: IDLE → LOADED → PLAYING → ENDED", "[playback]") {
    TestDevice dev(5);
    CHECK(dev.state() == DeviceState::IDLE);

    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));
    CHECK(dev.state() == DeviceState::LOADED);
    CHECK(dev.duration() == Catch::Approx(5.0f));

    dev.set_time(0);
    dev.handle_start(0);
    CHECK(dev.state() == DeviceState::PLAYING);

    // Tick past duration
    dev.set_time(5'100'000);  // 5.1 seconds
    bool active = dev.tick_once();
    CHECK_FALSE(active);
    CHECK(dev.state() == DeviceState::ENDED);
}

// ---------------------------------------------------------------------------
// Paint → shift frame-by-frame (centerpiece test)
// ---------------------------------------------------------------------------

TEST_CASE("Paint→shift frame-by-frame RGB output", "[playback][shift]") {
    TestDevice dev(5);
    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));

    dev.set_time(0);
    dev.handle_start(0);

    // Tick at t=0.5 to let paint render (fills layer buffer)
    dev.set_time(500'000);
    CHECK(dev.tick_once());

    // t=1.0: shift activates, snapshots paint, t_rel=0 → no shift
    // Expected: [51, 102, 153, 204, 255]
    dev.set_time(1'000'000);
    CHECK(dev.tick_once());
    const uint8_t* rgb = dev.rgb_data();
    check_pixel(rgb, 0, 51);
    check_pixel(rgb, 1, 102);
    check_pixel(rgb, 2, 153);
    check_pixel(rgb, 3, 204);
    check_pixel(rgb, 4, 255);

    // t=2.0: shifted right by 1 → [0, 51, 102, 153, 204]
    dev.set_time(2'000'000);
    CHECK(dev.tick_once());
    rgb = dev.rgb_data();
    check_pixel(rgb, 0, 0);
    check_pixel(rgb, 1, 51);
    check_pixel(rgb, 2, 102);
    check_pixel(rgb, 3, 153);
    check_pixel(rgb, 4, 204);

    // t=3.0: shifted right by 2 → [0, 0, 51, 102, 153]
    dev.set_time(3'000'000);
    CHECK(dev.tick_once());
    rgb = dev.rgb_data();
    check_pixel(rgb, 0, 0);
    check_pixel(rgb, 1, 0);
    check_pixel(rgb, 2, 51);
    check_pixel(rgb, 3, 102);
    check_pixel(rgb, 4, 153);

    // t=4.0: shifted right by 3 → [0, 0, 0, 51, 102]
    dev.set_time(4'000'000);
    CHECK(dev.tick_once());
    rgb = dev.rgb_data();
    check_pixel(rgb, 0, 0);
    check_pixel(rgb, 1, 0);
    check_pixel(rgb, 2, 0);
    check_pixel(rgb, 3, 51);
    check_pixel(rgb, 4, 102);
}

// ---------------------------------------------------------------------------
// Pause / resume
// ---------------------------------------------------------------------------

TEST_CASE("Pause and resume", "[playback][pause]") {
    TestDevice dev(5);
    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));

    dev.set_time(0);
    dev.handle_start(0);

    // Tick at t=0.5 (paint renders)
    dev.set_time(500'000);
    CHECK(dev.tick_once());

    // Tick at t=1.0 (shift activates)
    dev.set_time(1'000'000);
    CHECK(dev.tick_once());

    // Pause at t=1.5
    dev.set_time(1'500'000);
    dev.handle_pause();
    CHECK(dev.state() == DeviceState::PAUSED);
    CHECK(dev.current_t_rel() == Catch::Approx(1.5f));

    // tick_once while paused returns true but doesn't change output
    dev.set_time(2'000'000);
    CHECK(dev.tick_once());
    CHECK(dev.state() == DeviceState::PAUSED);

    // Resume: t0 adjusted so current_t_rel continues from 1.5
    // t0 = now - paused_t_rel * 1e6 = 2'000'000 - 1'500'000 = 500'000
    dev.handle_resume(500'000);
    CHECK(dev.state() == DeviceState::PLAYING);

    // Tick at real time 2'500'000 → t_rel = (2'500'000 - 500'000)/1e6 = 2.0
    // Expected: shifted right by 1 → [0, 51, 102, 153, 204]
    dev.set_time(2'500'000);
    CHECK(dev.tick_once());
    const uint8_t* rgb = dev.rgb_data();
    check_pixel(rgb, 0, 0);
    check_pixel(rgb, 1, 51);
    check_pixel(rgb, 2, 102);
    check_pixel(rgb, 3, 153);
    check_pixel(rgb, 4, 204);
}

// ---------------------------------------------------------------------------
// Reset on re-start from ENDED
// ---------------------------------------------------------------------------

TEST_CASE("Re-start from ENDED replays from beginning", "[playback]") {
    TestDevice dev(5);
    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));

    dev.set_time(0);
    dev.handle_start(0);

    // Run to completion
    dev.set_time(5'100'000);
    CHECK_FALSE(dev.tick_once());
    CHECK(dev.state() == DeviceState::ENDED);

    // Re-start
    dev.set_time(6'000'000);
    dev.handle_start(6'000'000);
    CHECK(dev.state() == DeviceState::PLAYING);

    // Tick at t_rel=0.5 (paint should render)
    dev.set_time(6'500'000);
    CHECK(dev.tick_once());

    // Tick at t_rel=1.0 (shift activates)
    dev.set_time(7'000'000);
    CHECK(dev.tick_once());
    const uint8_t* rgb = dev.rgb_data();
    check_pixel(rgb, 0, 51);
    check_pixel(rgb, 1, 102);
    check_pixel(rgb, 2, 153);
    check_pixel(rgb, 3, 204);
    check_pixel(rgb, 4, 255);
}

// ---------------------------------------------------------------------------
// handle_stop
// ---------------------------------------------------------------------------

TEST_CASE("Stop clears to black and transitions to LOADED", "[playback][stop]") {
    TestDevice dev(5);
    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));

    dev.set_time(0);
    dev.handle_start(0);

    // Render some content
    dev.set_time(1'000'000);
    dev.tick_once();

    // Stop
    dev.handle_stop();
    CHECK(dev.state() == DeviceState::LOADED);

    // RGB buffer should be all zeros
    const uint8_t* rgb = dev.rgb_data();
    for (int i = 0; i < 5 * 3; i++) {
        CHECK(rgb[i] == 0);
    }

    // Can restart
    dev.set_time(2'000'000);
    dev.handle_start(2'000'000);
    CHECK(dev.state() == DeviceState::PLAYING);

    // Render paint at t_rel=0.5
    dev.set_time(2'500'000);
    CHECK(dev.tick_once());
}

// ---------------------------------------------------------------------------
// handle_jump
// ---------------------------------------------------------------------------

TEST_CASE("Jump to t=0 from LOADED renders paint frame", "[playback][jump]") {
    TestDevice dev(5);
    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));

    // Jump to t=0 from LOADED state
    dev.handle_jump(0, 0.0f, 2);
    CHECK(dev.state() == DeviceState::PAUSED);

    // Paint is active at t=0, should show painted pixels
    const uint8_t* rgb = dev.rgb_data();
    check_pixel(rgb, 0, 51);
    check_pixel(rgb, 1, 102);
    check_pixel(rgb, 2, 153);
    check_pixel(rgb, 3, 204);
    check_pixel(rgb, 4, 255);
}

TEST_CASE("Jump clamps negative t_rel to 0", "[playback][jump]") {
    TestDevice dev(5);
    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));

    dev.handle_jump(0, -1.0f, 2);
    CHECK(dev.state() == DeviceState::PAUSED);
    CHECK(dev.current_t_rel() == Catch::Approx(0.0f));
}

TEST_CASE("Jump while PLAYING resets and continues from new timebase", "[playback][jump]") {
    TestDevice dev(5);
    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));

    dev.set_time(0);
    dev.handle_start(0);

    // Play to t=3.0 (shift has been running)
    dev.set_time(500'000);
    dev.tick_once();  // paint renders
    dev.set_time(3'000'000);
    dev.tick_once();

    // Jump to t_rel=0.0 while PLAYING.
    // Controller sets t0 = now = 3'000'000 so next tick starts from program t=0.
    // Clock stays monotonic: all subsequent set_time() values > 3'000'000.
    dev.handle_jump(3'000'000, 0.0f, 2);
    CHECK(dev.state() == DeviceState::PLAYING);

    // First tick after jump: now=3'500'000, t_rel = (3.5M - 3M)/1e6 = 0.5
    // Paint is active (0-1s) → renders [51, 102, 153, 204, 255]
    dev.set_time(3'500'000);
    dev.tick_once();
    const uint8_t* rgb = dev.rgb_data();
    check_pixel(rgb, 0, 51);
    check_pixel(rgb, 1, 102);
    check_pixel(rgb, 2, 153);
    check_pixel(rgb, 3, 204);
    check_pixel(rgb, 4, 255);

    // Continue to t_rel=2.0: now=5'000'000, t_rel = (5M - 3M)/1e6 = 2.0
    // Paint finished at 1.0, shift activated at 1.0 (snapshots paint buffer),
    // shift t_rel = 1.0 → shifted right by 1 → [0, 51, 102, 153, 204]
    dev.set_time(5'000'000);
    dev.tick_once();
    rgb = dev.rgb_data();
    check_pixel(rgb, 0, 0);
    check_pixel(rgb, 1, 51);
    check_pixel(rgb, 2, 102);
    check_pixel(rgb, 3, 153);
    check_pixel(rgb, 4, 204);
}

TEST_CASE("Jump at or past duration is no-op", "[playback][jump]") {
    TestDevice dev(5);
    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));

    dev.handle_jump(0, 5.0f, 2);
    // Should remain LOADED (no-op)
    CHECK(dev.state() == DeviceState::LOADED);

    dev.handle_jump(0, 10.0f, 2);
    CHECK(dev.state() == DeviceState::LOADED);
}

// ---------------------------------------------------------------------------
// Load failure
// ---------------------------------------------------------------------------

TEST_CASE("Load failure: garbage blob", "[playback][load]") {
    TestDevice dev(5);
    uint8_t garbage[] = {0xDE, 0xAD, 0xBE, 0xEF};
    CHECK_FALSE(dev.handle_load(garbage, sizeof(garbage), 1));
    CHECK(dev.state() == DeviceState::IDLE);
    CHECK(dev.duration() == Catch::Approx(0.0f));

    // RGB buffer should be all zeros
    const uint8_t* rgb = dev.rgb_data();
    for (int i = 0; i < 5 * 3; i++) {
        CHECK(rgb[i] == 0);
    }
}

TEST_CASE("Load failure after playing pushes black to output", "[playback][load]") {
    TestDevice dev(5);
    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));

    dev.set_time(0);
    dev.handle_start(0);
    dev.set_time(500'000);
    dev.tick_once();

    // Verify something was rendered (non-black)
    bool any_nonzero = false;
    for (int i = 0; i < 5 * 3; i++) {
        if (dev.rgb_data()[i] != 0) { any_nonzero = true; break; }
    }
    REQUIRE(any_nonzero);

    int before = dev.output_frame_count;

    // Load garbage — should clear buffer and push black frame
    uint8_t garbage[] = {0xDE, 0xAD, 0xBE, 0xEF};
    CHECK_FALSE(dev.handle_load(garbage, sizeof(garbage), 2));
    CHECK(dev.state() == DeviceState::IDLE);
    CHECK(dev.output_frame_count == before + 1);

    // Buffer is black
    for (int i = 0; i < 5 * 3; i++) {
        CHECK(dev.rgb_data()[i] == 0);
    }
}

// ---------------------------------------------------------------------------
// Double load
// ---------------------------------------------------------------------------

TEST_CASE("Double load replaces program cleanly", "[playback][load]") {
    TestDevice dev(5);
    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));

    dev.set_time(0);
    dev.handle_start(0);
    dev.set_time(500'000);
    dev.tick_once();  // render some content

    // Load again — should replace
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 2));
    CHECK(dev.state() == DeviceState::LOADED);

    // RGB buffer should be zeroed
    const uint8_t* rgb = dev.rgb_data();
    for (int i = 0; i < 5 * 3; i++) {
        CHECK(rgb[i] == 0);
    }

    // Can start the new program
    dev.set_time(1'000'000);
    dev.handle_start(1'000'000);
    CHECK(dev.state() == DeviceState::PLAYING);
}

// ---------------------------------------------------------------------------
// Sync offset
// ---------------------------------------------------------------------------

TEST_CASE("Sync offset shifts computed t_rel", "[playback][sync]") {
    TestDevice dev(5);
    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));

    dev.set_time(0);
    dev.handle_start(0);

    // Tick at t=0.5 (paint renders, fills buffer)
    dev.set_time(500'000);
    dev.tick_once();

    // Set sync offset = +500'000 us (0.5s ahead)
    // So at wall clock 500'000, effective t_rel = (500'000 + 500'000 - 0) / 1e6 = 1.0
    dev.handle_sync_result(500'000);

    dev.set_time(500'000);
    CHECK(dev.current_t_rel() == Catch::Approx(1.0f));

    // At wall clock 1'500'000, effective t_rel = (1'500'000 + 500'000 - 0)/1e6 = 2.0
    dev.set_time(1'500'000);
    CHECK(dev.tick_once());
    const uint8_t* rgb = dev.rgb_data();
    // t_rel=2.0: shift by 1 → [0, 51, 102, 153, 204]
    check_pixel(rgb, 0, 0);
    check_pixel(rgb, 1, 51);
    check_pixel(rgb, 2, 102);
    check_pixel(rgb, 3, 153);
    check_pixel(rgb, 4, 204);
}

// ---------------------------------------------------------------------------
// current_t_rel() by state
// ---------------------------------------------------------------------------

TEST_CASE("current_t_rel semantics per state", "[playback]") {
    TestDevice dev(5);

    // IDLE → 0
    CHECK(dev.current_t_rel() == Catch::Approx(0.0f));

    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));

    // LOADED → 0
    CHECK(dev.current_t_rel() == Catch::Approx(0.0f));

    // PLAYING → live value
    dev.set_time(0);
    dev.handle_start(0);
    dev.set_time(1'500'000);
    CHECK(dev.current_t_rel() == Catch::Approx(1.5f));

    // Clamp to [0, duration]
    dev.set_time(6'000'000);
    CHECK(dev.current_t_rel() == Catch::Approx(5.0f));

    // PAUSED → frozen value
    dev.set_time(2'000'000);
    // Need to re-load to get back to a clean playing state
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));
    dev.set_time(0);
    dev.handle_start(0);
    dev.set_time(2'000'000);
    dev.handle_pause();
    CHECK(dev.current_t_rel() == Catch::Approx(2.0f));
    dev.set_time(3'000'000);  // time advances but t_rel stays frozen
    CHECK(dev.current_t_rel() == Catch::Approx(2.0f));

    // ENDED → duration
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));
    dev.set_time(0);
    dev.handle_start(0);
    dev.set_time(5'100'000);
    dev.tick_once();
    CHECK(dev.state() == DeviceState::ENDED);
    CHECK(dev.current_t_rel() == Catch::Approx(5.0f));
}

// ---------------------------------------------------------------------------
// tick_once return values by state
// ---------------------------------------------------------------------------

TEST_CASE("tick_once returns correct values per state", "[playback]") {
    TestDevice dev(5);

    // IDLE → false
    CHECK_FALSE(dev.tick_once());

    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));

    // LOADED → true
    CHECK(dev.tick_once());

    // PLAYING → true (active)
    dev.set_time(0);
    dev.handle_start(0);
    dev.set_time(500'000);
    CHECK(dev.tick_once());

    // PAUSED → true
    dev.handle_pause();
    CHECK(dev.tick_once());

    // ENDED → false
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));
    dev.set_time(0);
    dev.handle_start(0);
    dev.set_time(5'100'000);
    dev.tick_once();  // transitions to ENDED
    CHECK_FALSE(dev.tick_once());
}

// ---------------------------------------------------------------------------
// Scheduled future start (t_rel < 0)
// ---------------------------------------------------------------------------

TEST_CASE("tick_once with future start returns true without rendering", "[playback]") {
    TestDevice dev(5);
    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));

    // Start 1 second in the future
    dev.set_time(0);
    dev.handle_start(1'000'000);  // t0 = 1s from now
    CHECK(dev.state() == DeviceState::PLAYING);

    // At t=0, t_rel = (0 - 1'000'000)/1e6 = -1.0 → return true, no render
    CHECK(dev.tick_once());

    // Buffer should still be zeros (no render happened)
    const uint8_t* rgb = dev.rgb_data();
    for (int i = 0; i < 5 * 3; i++) {
        CHECK(rgb[i] == 0);
    }
}

// ---------------------------------------------------------------------------
// Ignore commands in wrong states
// ---------------------------------------------------------------------------

TEST_CASE("Commands ignored in wrong states", "[playback]") {
    TestDevice dev(5);

    // start/pause/resume/stop from IDLE — all ignored
    dev.handle_start(0);
    CHECK(dev.state() == DeviceState::IDLE);
    dev.handle_pause();
    CHECK(dev.state() == DeviceState::IDLE);
    dev.handle_resume(0);
    CHECK(dev.state() == DeviceState::IDLE);
    dev.handle_stop();
    CHECK(dev.state() == DeviceState::IDLE);

    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));

    // pause from LOADED — ignored
    dev.handle_pause();
    CHECK(dev.state() == DeviceState::LOADED);

    // resume from LOADED — ignored
    dev.handle_resume(0);
    CHECK(dev.state() == DeviceState::LOADED);

    // start from PLAYING — ignored
    dev.set_time(0);
    dev.handle_start(0);
    dev.handle_start(0);
    CHECK(dev.state() == DeviceState::PLAYING);

    // resume from PLAYING — ignored
    dev.handle_resume(0);
    CHECK(dev.state() == DeviceState::PLAYING);
}

// ---------------------------------------------------------------------------
// Telemetry reporting
// ---------------------------------------------------------------------------

TEST_CASE("Telemetry across full lifecycle", "[playback][telemetry]") {
    TestDevice dev(5);
    auto blob = load_blob(SHIFT_FIXTURE);

    // Load → LOADED
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));
    REQUIRE(dev.telemetry.size() == 1);
    CHECK(dev.last_telemetry().state == DeviceState::LOADED);
    CHECK(dev.last_telemetry().t == Catch::Approx(0.0f));
    CHECK(dev.last_telemetry().err.empty());

    // Start → PLAYING
    dev.set_time(0);
    dev.handle_start(0);
    REQUIRE(dev.telemetry.size() == 2);
    CHECK(dev.last_telemetry().state == DeviceState::PLAYING);
    CHECK(dev.last_telemetry().t == Catch::Approx(0.0f));

    // Tick (no telemetry emitted per frame)
    dev.set_time(500'000);
    dev.tick_once();
    CHECK(dev.telemetry.size() == 2);

    // Pause → PAUSED with current t_rel
    dev.set_time(1'500'000);
    dev.handle_pause();
    REQUIRE(dev.telemetry.size() == 3);
    CHECK(dev.last_telemetry().state == DeviceState::PAUSED);
    CHECK(dev.last_telemetry().t == Catch::Approx(1.5f));

    // Resume → PLAYING with paused t_rel
    dev.handle_resume(1'500'000);
    REQUIRE(dev.telemetry.size() == 4);
    CHECK(dev.last_telemetry().state == DeviceState::PLAYING);
    CHECK(dev.last_telemetry().t == Catch::Approx(1.5f));

    // Stop → LOADED
    dev.handle_stop();
    REQUIRE(dev.telemetry.size() == 5);
    CHECK(dev.last_telemetry().state == DeviceState::LOADED);
    CHECK(dev.last_telemetry().t == Catch::Approx(0.0f));

    // Start again, run to ENDED
    dev.set_time(2'000'000);
    dev.handle_start(2'000'000);
    REQUIRE(dev.telemetry.size() == 6);
    CHECK(dev.last_telemetry().state == DeviceState::PLAYING);

    dev.set_time(2'500'000);
    dev.tick_once();  // paint renders
    dev.set_time(7'100'000);
    dev.tick_once();  // past duration → ENDED
    REQUIRE(dev.telemetry.size() == 7);
    CHECK(dev.last_telemetry().state == DeviceState::ENDED);
    CHECK(dev.last_telemetry().t == Catch::Approx(5.0f));
}

TEST_CASE("Telemetry on load failure includes error", "[playback][telemetry]") {
    TestDevice dev(5);
    uint8_t garbage[] = {0xDE, 0xAD, 0xBE, 0xEF};
    dev.handle_load(garbage, sizeof(garbage), 1);
    REQUIRE(dev.telemetry.size() == 1);
    CHECK(dev.last_telemetry().state == DeviceState::IDLE);
    CHECK(dev.last_telemetry().err == "decode failed");
}

TEST_CASE("No telemetry from ignored commands", "[playback][telemetry]") {
    TestDevice dev(5);

    // Commands from IDLE — all ignored, no telemetry
    dev.handle_start(0);
    dev.handle_pause();
    dev.handle_resume(0);
    dev.handle_stop();
    CHECK(dev.telemetry.empty());

    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));
    size_t baseline = dev.telemetry.size();

    // Pause/resume from LOADED — ignored
    dev.handle_pause();
    dev.handle_resume(0);
    CHECK(dev.telemetry.size() == baseline);
}
