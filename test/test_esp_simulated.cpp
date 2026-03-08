#include <catch2/catch_test_macros.hpp>
#include <catch2/catch_approx.hpp>
#include "../src/esp_simulated.h"

#include <cstdio>
#include <cstring>
#include <map>
#include <vector>

// ---------------------------------------------------------------------------
// ControlledESPSimulated — deterministic clock for testing
// ---------------------------------------------------------------------------

class ControlledESPSimulated : public ESPSimulated {
public:
    ControlledESPSimulated(uint16_t len) : ESPSimulated(len) {}

    void set_time(int64_t us) { _now = us; }
    int64_t now_mono() const override { return _now; }

    uint32_t frame_index() const { return _frame_index; }

private:
    int64_t _now = 0;
};

// ---------------------------------------------------------------------------
// MiniSimController — test-local frame assembler
// ---------------------------------------------------------------------------

struct ProgramFrame {
    uint32_t frame_index;
    float t_rel;
    std::vector<std::vector<uint8_t>> strips;  // canonical order
};

class MiniSimController {
public:
    explicit MiniSimController(size_t strip_count)
        : _strip_count(strip_count) {}

    void feed(size_t strip_index, const SimRgbFrame& frame) {
        auto& bucket = _buckets[frame.frame_index];
        if (bucket.strips.empty()) {
            bucket.frame_index = frame.frame_index;
            bucket.t_rel = frame.t_rel;
            bucket.strips.resize(_strip_count);
        }
        bucket.strips[strip_index] = frame.rgb;
        bucket.present++;

        if (bucket.present == _strip_count) {
            ProgramFrame pf;
            pf.frame_index = bucket.frame_index;
            pf.t_rel = bucket.t_rel;
            pf.strips = std::move(bucket.strips);
            _complete.push_back(std::move(pf));
            _buckets.erase(frame.frame_index);
        }
    }

    std::vector<ProgramFrame> drain() {
        std::vector<ProgramFrame> out;
        out.swap(_complete);
        return out;
    }

    size_t pending_count() const { return _buckets.size(); }

private:
    struct Bucket {
        uint32_t frame_index = 0;
        float t_rel = 0.0f;
        std::vector<std::vector<uint8_t>> strips;
        size_t present = 0;
    };

    size_t _strip_count;
    std::map<uint32_t, Bucket> _buckets;
    std::vector<ProgramFrame> _complete;
};

// ---------------------------------------------------------------------------
// Helpers
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
static const char* DUAL_LEFT = "test/fixtures/test_dual_shift_left.bin";
static const char* DUAL_RIGHT = "test/fixtures/test_dual_shift_right.bin";

// Check that pixel i in an rgb buffer has R=G=B=expected
static void check_pixel(const uint8_t* rgb, int i, uint8_t expected) {
    INFO("pixel " << i);
    CHECK(rgb[i * 3 + 0] == expected);
    CHECK(rgb[i * 3 + 1] == expected);
    CHECK(rgb[i * 3 + 2] == expected);
}

static void check_pixel_vec(const std::vector<uint8_t>& rgb, int i, uint8_t expected) {
    check_pixel(rgb.data(), i, expected);
}

// =========================================================================
// ESPSimulated unit tests
// =========================================================================

TEST_CASE("ESPSimulated construction", "[espsim]") {
    ControlledESPSimulated dev(5);
    CHECK(dev.state() == DeviceState::IDLE);
    CHECK(dev.strip_length() == 5);
    CHECK(dev.drain_frames().empty());
    CHECK(dev.drain_telemetry().empty());
}

TEST_CASE("Frame emission on normal playback tick", "[espsim]") {
    ControlledESPSimulated dev(5);
    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));

    dev.set_time(0);
    dev.handle_start(0);
    dev.drain_telemetry();  // clear load+start telemetry

    // Tick at t=0.5 to let paint render (fills layer buffer for shift snapshot)
    dev.set_time(500'000);
    dev.tick_once();
    auto frames = dev.drain_frames();
    REQUIRE(frames.size() == 1);
    CHECK(frames[0].gen == 1);
    CHECK(frames[0].frame_index == 0);
    CHECK(frames[0].t_rel == Catch::Approx(0.5f));

    // Tick at t=1.0 — shift activates, snapshots paint, no shift yet
    dev.set_time(1'000'000);
    dev.tick_once();

    frames = dev.drain_frames();
    REQUIRE(frames.size() == 1);
    CHECK(frames[0].gen == 1);
    CHECK(frames[0].frame_index == 1);
    CHECK(frames[0].t_rel == Catch::Approx(1.0f));
    REQUIRE(frames[0].rgb.size() == 15);

    // Verify RGB: shift snapshot of paint → [51, 102, 153, 204, 255]
    check_pixel_vec(frames[0].rgb, 0, 51);
    check_pixel_vec(frames[0].rgb, 1, 102);
    check_pixel_vec(frames[0].rgb, 2, 153);
    check_pixel_vec(frames[0].rgb, 3, 204);
    check_pixel_vec(frames[0].rgb, 4, 255);

    // Tick at t=2.0 — shift has moved pixels right by 1
    dev.set_time(2'000'000);
    dev.tick_once();

    frames = dev.drain_frames();
    REQUIRE(frames.size() == 1);
    CHECK(frames[0].gen == 1);
    CHECK(frames[0].frame_index == 2);
    CHECK(frames[0].t_rel == Catch::Approx(2.0f));
    check_pixel_vec(frames[0].rgb, 0, 0);
    check_pixel_vec(frames[0].rgb, 1, 51);
    check_pixel_vec(frames[0].rgb, 2, 102);
    check_pixel_vec(frames[0].rgb, 3, 153);
    check_pixel_vec(frames[0].rgb, 4, 204);
}

TEST_CASE("No frame for future-start pre-roll", "[espsim]") {
    ControlledESPSimulated dev(5);
    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));

    // Start with t0 = 2,000,000us (2 seconds in the future)
    dev.set_time(0);
    dev.handle_start(2'000'000);
    dev.drain_telemetry();
    dev.drain_frames();

    // Tick at t=1s — before t0, no frame emitted
    dev.set_time(1'000'000);
    dev.tick_once();
    CHECK(dev.drain_frames().empty());

    // Tick at t=3s — now past t0, t_rel = 1.0
    dev.set_time(3'000'000);
    dev.tick_once();
    auto frames = dev.drain_frames();
    REQUIRE(frames.size() == 1);
    CHECK(frames[0].t_rel == Catch::Approx(1.0f));
}

TEST_CASE("Telemetry queue records state transitions", "[espsim]") {
    ControlledESPSimulated dev(5);
    auto blob = load_blob(SHIFT_FIXTURE);

    // Load
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));
    auto tel = dev.drain_telemetry();
    REQUIRE(tel.size() == 1);
    CHECK(tel[0].state == DeviceState::LOADED);
    CHECK(tel[0].t_rel == 0.0f);

    // Start
    dev.set_time(0);
    dev.handle_start(0);
    tel = dev.drain_telemetry();
    REQUIRE(tel.size() == 1);
    CHECK(tel[0].state == DeviceState::PLAYING);

    // Pause
    dev.set_time(1'000'000);
    dev.tick_once();  // render a frame so there's a position
    dev.handle_pause();
    tel = dev.drain_telemetry();
    REQUIRE(tel.size() == 1);
    CHECK(tel[0].state == DeviceState::PAUSED);

    // Resume
    dev.handle_resume(1'000'000);
    tel = dev.drain_telemetry();
    REQUIRE(tel.size() == 1);
    CHECK(tel[0].state == DeviceState::PLAYING);

    // Stop
    dev.handle_stop();
    tel = dev.drain_telemetry();
    REQUIRE(tel.size() == 1);
    CHECK(tel[0].state == DeviceState::LOADED);
}

TEST_CASE("Telemetry on load failure", "[espsim]") {
    ControlledESPSimulated dev(5);
    uint8_t garbage[] = {0xDE, 0xAD};
    CHECK_FALSE(dev.handle_load(garbage, 2, 1));

    auto tel = dev.drain_telemetry();
    REQUIRE(tel.size() == 1);
    CHECK(tel[0].state == DeviceState::IDLE);
    CHECK(tel[0].error == "decode failed");
}

TEST_CASE("Telemetry on program end", "[espsim]") {
    ControlledESPSimulated dev(5);
    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));
    dev.set_time(0);
    dev.handle_start(0);
    dev.drain_telemetry();

    // Tick past duration (5.0s)
    dev.set_time(5'100'000);
    dev.tick_once();

    auto tel = dev.drain_telemetry();
    REQUIRE(tel.size() == 1);
    CHECK(tel[0].state == DeviceState::ENDED);
}

// =========================================================================
// debug_seek tests
// =========================================================================

TEST_CASE("debug_seek from LOADED transitions to PAUSED", "[espsim][debug]") {
    ControlledESPSimulated dev(5);
    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));
    dev.drain_frames();

    dev.debug_seek(1.0f);
    CHECK(dev.state() == DeviceState::PAUSED);

    auto frames = dev.drain_frames();
    REQUIRE(frames.size() == 1);

    // At t=1.0: paint fills [51, 102, 153, 204, 255]
    check_pixel_vec(frames[0].rgb, 0, 51);
    check_pixel_vec(frames[0].rgb, 4, 255);
}

TEST_CASE("debug_seek from PAUSED stays PAUSED", "[espsim][debug]") {
    ControlledESPSimulated dev(5);
    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));

    dev.debug_seek(1.0f);
    CHECK(dev.state() == DeviceState::PAUSED);
    dev.drain_frames();

    dev.debug_seek(2.0f);
    CHECK(dev.state() == DeviceState::PAUSED);

    auto frames = dev.drain_frames();
    REQUIRE(frames.size() == 1);

    // At t=2.0: shifted right by 1 → [0, 51, 102, 153, 204]
    check_pixel_vec(frames[0].rgb, 0, 0);
    check_pixel_vec(frames[0].rgb, 1, 51);
    check_pixel_vec(frames[0].rgb, 4, 204);
}

TEST_CASE("debug_seek from PLAYING stays PLAYING", "[espsim][debug]") {
    ControlledESPSimulated dev(5);
    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));

    dev.set_time(0);
    dev.handle_start(0);
    dev.set_time(500'000);
    dev.tick_once();
    CHECK(dev.state() == DeviceState::PLAYING);
    dev.drain_frames();

    dev.debug_seek(2.0f);
    CHECK(dev.state() == DeviceState::PLAYING);

    auto frames = dev.drain_frames();
    REQUIRE(frames.size() == 1);
    check_pixel_vec(frames[0].rgb, 0, 0);
    check_pixel_vec(frames[0].rgb, 1, 51);
}

TEST_CASE("debug_seek clamps to boundaries", "[espsim][debug]") {
    ControlledESPSimulated dev(5);
    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));

    // Negative clamps to 0
    dev.debug_seek(-5.0f);
    CHECK(dev.state() == DeviceState::PAUSED);
    dev.drain_frames();

    // Beyond duration clamps to duration
    dev.debug_seek(999.0f);
    CHECK(dev.state() == DeviceState::PAUSED);
    auto frames = dev.drain_frames();
    REQUIRE(frames.size() == 1);
}

TEST_CASE("debug_seek no-op without engine", "[espsim][debug]") {
    ControlledESPSimulated dev(5);
    dev.debug_seek(1.0f);  // no program loaded
    CHECK(dev.state() == DeviceState::IDLE);
    CHECK(dev.drain_frames().empty());
}

TEST_CASE("debug_seek increments frame_index", "[espsim][debug]") {
    ControlledESPSimulated dev(5);
    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));
    CHECK(dev.frame_index() == 0);

    dev.debug_seek(1.0f);
    CHECK(dev.frame_index() == 1);

    dev.debug_seek(2.0f);
    CHECK(dev.frame_index() == 2);
}

// =========================================================================
// debug_step tests
// =========================================================================

TEST_CASE("debug_step from PAUSED advances by 1/50s", "[espsim][debug]") {
    ControlledESPSimulated dev(5);
    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));

    dev.debug_seek(1.0f);
    dev.drain_frames();
    CHECK(dev.state() == DeviceState::PAUSED);

    dev.debug_step(1);
    CHECK(dev.state() == DeviceState::PAUSED);
    auto frames = dev.drain_frames();
    REQUIRE(frames.size() == 1);
}

TEST_CASE("debug_step from LOADED transitions to PAUSED", "[espsim][debug]") {
    ControlledESPSimulated dev(5);
    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));

    dev.debug_step(1);
    CHECK(dev.state() == DeviceState::PAUSED);
    auto frames = dev.drain_frames();
    REQUIRE(frames.size() == 1);
}

TEST_CASE("debug_step clamps at boundaries", "[espsim][debug]") {
    ControlledESPSimulated dev(5);
    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));

    // Step backward from 0 — stays at 0
    dev.debug_seek(0.0f);
    dev.drain_frames();
    dev.debug_step(-1);
    auto frames = dev.drain_frames();
    REQUIRE(frames.size() == 1);
    CHECK(dev.state() == DeviceState::PAUSED);
}

TEST_CASE("debug_step ignored in invalid states", "[espsim][debug]") {
    ControlledESPSimulated dev(5);

    // IDLE — no-op
    dev.debug_step(1);
    CHECK(dev.state() == DeviceState::IDLE);
    CHECK(dev.drain_frames().empty());

    // PLAYING — no-op
    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));
    dev.set_time(0);
    dev.handle_start(0);
    dev.set_time(500'000);
    dev.tick_once();
    CHECK(dev.state() == DeviceState::PLAYING);
    dev.drain_frames();

    dev.debug_step(1);
    CHECK(dev.state() == DeviceState::PLAYING);
    CHECK(dev.drain_frames().empty());
}

TEST_CASE("debug_step increments frame_index", "[espsim][debug]") {
    ControlledESPSimulated dev(5);
    auto blob = load_blob(SHIFT_FIXTURE);
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));
    CHECK(dev.frame_index() == 0);

    dev.debug_seek(1.0f);
    CHECK(dev.frame_index() == 1);

    dev.debug_step(1);
    CHECK(dev.frame_index() == 2);

    dev.debug_step(1);
    CHECK(dev.frame_index() == 3);
}

// =========================================================================
// Integration: two-strip assembly via MiniSimController
// =========================================================================

TEST_CASE("Two-strip shared-start program frame assembly", "[espsim][integration]") {
    ControlledESPSimulated left(5);
    ControlledESPSimulated right(5);

    auto lblob = load_blob(DUAL_LEFT);
    auto rblob = load_blob(DUAL_RIGHT);
    REQUIRE(left.handle_load(lblob.data(), lblob.size(), 1));
    REQUIRE(right.handle_load(rblob.data(), rblob.size(), 1));

    int64_t t0 = 0;
    left.set_time(0);
    right.set_time(0);
    left.handle_start(t0);
    right.handle_start(t0);

    MiniSimController ctrl(2);

    // Tick both at t=0.5 to let paint render (fills layer buffers)
    left.set_time(500'000);
    right.set_time(500'000);
    left.tick_once();
    right.tick_once();
    left.drain_frames();
    right.drain_frames();

    // Tick both at t=1.0 — shift activates, snapshots paint
    left.set_time(1'000'000);
    right.set_time(1'000'000);
    left.tick_once();
    right.tick_once();

    auto lframes = left.drain_frames();
    auto rframes = right.drain_frames();
    REQUIRE(lframes.size() == 1);
    REQUIRE(rframes.size() == 1);

    ctrl.feed(0, lframes[0]);
    ctrl.feed(1, rframes[0]);

    auto pframes = ctrl.drain();
    REQUIRE(pframes.size() == 1);
    CHECK(pframes[0].frame_index == 1);
    CHECK(pframes[0].t_rel == Catch::Approx(1.0f));
    REQUIRE(pframes[0].strips.size() == 2);

    // Left strip at t=1.0: [51, 102, 153, 204, 255]
    check_pixel_vec(pframes[0].strips[0], 0, 51);
    check_pixel_vec(pframes[0].strips[0], 1, 102);
    check_pixel_vec(pframes[0].strips[0], 2, 153);
    check_pixel_vec(pframes[0].strips[0], 3, 204);
    check_pixel_vec(pframes[0].strips[0], 4, 255);

    // Right strip at t=1.0: [255, 204, 153, 102, 51]
    check_pixel_vec(pframes[0].strips[1], 0, 255);
    check_pixel_vec(pframes[0].strips[1], 1, 204);
    check_pixel_vec(pframes[0].strips[1], 2, 153);
    check_pixel_vec(pframes[0].strips[1], 3, 102);
    check_pixel_vec(pframes[0].strips[1], 4, 51);

    // Tick both at t=2.0 — shift has moved right by 1
    left.set_time(2'000'000);
    right.set_time(2'000'000);
    left.tick_once();
    right.tick_once();

    lframes = left.drain_frames();
    rframes = right.drain_frames();
    ctrl.feed(0, lframes[0]);
    ctrl.feed(1, rframes[0]);

    pframes = ctrl.drain();
    REQUIRE(pframes.size() == 1);
    CHECK(pframes[0].frame_index == 2);

    // Left t=2.0: [0, 51, 102, 153, 204]
    check_pixel_vec(pframes[0].strips[0], 0, 0);
    check_pixel_vec(pframes[0].strips[0], 1, 51);
    check_pixel_vec(pframes[0].strips[0], 4, 204);

    // Right t=2.0: [0, 255, 204, 153, 102]
    check_pixel_vec(pframes[0].strips[1], 0, 0);
    check_pixel_vec(pframes[0].strips[1], 1, 255);
    check_pixel_vec(pframes[0].strips[1], 4, 102);
}

TEST_CASE("Complete-only assembly: partial does not emit", "[espsim][integration]") {
    ControlledESPSimulated left(5);
    ControlledESPSimulated right(5);

    auto lblob = load_blob(DUAL_LEFT);
    auto rblob = load_blob(DUAL_RIGHT);
    REQUIRE(left.handle_load(lblob.data(), lblob.size(), 1));
    REQUIRE(right.handle_load(rblob.data(), rblob.size(), 1));

    left.set_time(0);
    right.set_time(0);
    left.handle_start(0);
    right.handle_start(0);

    MiniSimController ctrl(2);

    // Tick both at t=0.5 to let paint render
    left.set_time(500'000);
    right.set_time(500'000);
    left.tick_once();
    right.tick_once();
    left.drain_frames();
    right.drain_frames();

    // Tick only left at t=1.0
    left.set_time(1'000'000);
    left.tick_once();
    auto lframes = left.drain_frames();
    REQUIRE(lframes.size() == 1);
    ctrl.feed(0, lframes[0]);

    // No complete frame yet
    CHECK(ctrl.drain().empty());
    CHECK(ctrl.pending_count() == 1);

    // Now tick right — completes the frame
    right.set_time(1'000'000);
    right.tick_once();
    auto rframes = right.drain_frames();
    ctrl.feed(1, rframes[0]);

    auto pframes = ctrl.drain();
    REQUIRE(pframes.size() == 1);
    CHECK(ctrl.pending_count() == 0);
}

TEST_CASE("Generation propagation after load and jump", "[espsim][integration]") {
    ControlledESPSimulated dev(5);
    auto blob = load_blob(SHIFT_FIXTURE);

    // Load with gen=1
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 1));
    dev.set_time(0);
    dev.handle_start(0);
    dev.set_time(1'000'000);
    dev.tick_once();

    auto frames = dev.drain_frames();
    REQUIRE(frames.size() == 1);
    CHECK(frames[0].gen == 1);

    // Jump with gen=2
    dev.handle_jump(0, 0.0f, 2);
    dev.set_time(1'000'000);
    dev.tick_once();

    frames = dev.drain_frames();
    REQUIRE(frames.size() == 1);
    CHECK(frames[0].gen == 2);

    // Re-load with gen=5
    REQUIRE(dev.handle_load(blob.data(), blob.size(), 5));
    dev.set_time(0);
    dev.handle_start(0);
    dev.set_time(1'000'000);
    dev.tick_once();

    frames = dev.drain_frames();
    REQUIRE(frames.size() == 1);
    CHECK(frames[0].gen == 5);
}
