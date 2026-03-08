#include <catch2/catch_test_macros.hpp>
#include <catch2/catch_approx.hpp>
#include "../src/sim_controller.h"

#include <cstdio>
#include <vector>

// ---------------------------------------------------------------------------
// ControlledESPSimulated — deterministic clock for testing
// ---------------------------------------------------------------------------

class ControlledESPSimulated : public ESPSimulated {
public:
    ControlledESPSimulated(uint16_t len) : ESPSimulated(len) {}

    void set_time(int64_t us) { _now = us; }
    int64_t now_mono() const override { return _now; }

private:
    int64_t _now = 0;
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

static const char* DUAL_LEFT  = "test/fixtures/test_dual_shift_left.bin";
static const char* DUAL_RIGHT = "test/fixtures/test_dual_shift_right.bin";

static void check_pixel_vec(const std::vector<uint8_t>& rgb, int i, uint8_t expected) {
    INFO("pixel " << i);
    CHECK(rgb[i * 3 + 0] == expected);
    CHECK(rgb[i * 3 + 1] == expected);
    CHECK(rgb[i * 3 + 2] == expected);
}

struct DualFixture {
    ControlledESPSimulated left{5};
    ControlledESPSimulated right{5};
    std::vector<uint8_t> lblob;
    std::vector<uint8_t> rblob;

    DualFixture() {
        lblob = load_blob(DUAL_LEFT);
        rblob = load_blob(DUAL_RIGHT);
    }

    std::vector<ControllerStrip> strips() {
        return {
            {"left", 5, &left},
            {"right", 5, &right},
        };
    }

    CompiledProgram program(bool loop = false) {
        CompiledProgram prog;
        prog.artifact_id = "test";
        prog.duration = 5.0f;
        prog.loop = loop;
        prog.strips = {
            {"left", 5, lblob},
            {"right", 5, rblob},
        };
        return prog;
    }

    void set_time(int64_t us) {
        left.set_time(us);
        right.set_time(us);
    }
};

// Helper: find an event of a given kind in a list
static bool has_event(const std::vector<ControllerEvent>& evts, ControllerEvent::Kind k) {
    for (auto& e : evts)
        if (e.kind == k) return true;
    return false;
}

static bool has_state_event(const std::vector<ControllerEvent>& evts, ControllerState s) {
    for (auto& e : evts)
        if (e.kind == ControllerEvent::STATE_CHANGED && e.state == s) return true;
    return false;
}

// =========================================================================
// 1. Load and validation
// =========================================================================

TEST_CASE("Successful load of dual-strip program", "[simctrl]") {
    DualFixture f;
    SimController ctrl(f.strips());

    CHECK(ctrl.state() == ControllerState::IDLE);
    REQUIRE(ctrl.load(f.program()));

    CHECK(ctrl.state() == ControllerState::LOADED);
    CHECK(ctrl.session_id() == 1);
    CHECK(ctrl.epoch() == 0);
    CHECK(ctrl.duration() == Catch::Approx(5.0f));

    auto evts = ctrl.drain_events();
    CHECK(has_event(evts, ControllerEvent::SESSION_STARTED));
    CHECK(has_state_event(evts, ControllerState::LOADED));
}

TEST_CASE("Load increments session_id on successive loads", "[simctrl]") {
    DualFixture f;
    SimController ctrl(f.strips());

    REQUIRE(ctrl.load(f.program()));
    CHECK(ctrl.session_id() == 1);

    REQUIRE(ctrl.load(f.program()));
    CHECK(ctrl.session_id() == 2);
}

TEST_CASE("Strip ID mismatch fails load", "[simctrl]") {
    DualFixture f;
    SimController ctrl(f.strips());

    auto prog = f.program();
    prog.strips[0].strip_id = "wrong";
    CHECK_FALSE(ctrl.load(prog));

    CHECK(ctrl.state() == ControllerState::IDLE);
    auto evts = ctrl.drain_events();
    CHECK(has_event(evts, ControllerEvent::ERROR));
}

TEST_CASE("Strip length mismatch fails load", "[simctrl]") {
    DualFixture f;
    SimController ctrl(f.strips());

    auto prog = f.program();
    prog.strips[0].length = 10;
    CHECK_FALSE(ctrl.load(prog));

    auto evts = ctrl.drain_events();
    CHECK(has_event(evts, ControllerEvent::ERROR));
}

TEST_CASE("Order-independent strip matching succeeds", "[simctrl]") {
    DualFixture f;
    SimController ctrl(f.strips());

    // Program with strips in reverse order
    CompiledProgram prog;
    prog.artifact_id = "test";
    prog.duration = 5.0f;
    prog.strips = {
        {"right", 5, f.rblob},
        {"left", 5, f.lblob},
    };
    REQUIRE(ctrl.load(prog));
    CHECK(ctrl.state() == ControllerState::LOADED);

    // Verify correct device got correct blob by playing
    f.set_time(0);
    ctrl.play();
    f.set_time(500'000);
    ctrl.tick_once();
    ctrl.drain_program_frames();
    f.set_time(1'000'000);
    ctrl.tick_once();
    auto pf = ctrl.drain_program_frames();
    REQUIRE(pf.size() == 1);
    // Canonical order is [left, right] regardless of program order
    // Left at t=1.0: [51, 102, 153, 204, 255]
    check_pixel_vec(pf[0].strips[0], 0, 51);
    check_pixel_vec(pf[0].strips[0], 4, 255);
    // Right at t=1.0: [255, 204, 153, 102, 51]
    check_pixel_vec(pf[0].strips[1], 0, 255);
    check_pixel_vec(pf[0].strips[1], 4, 51);
}

TEST_CASE("Duplicate strip_id in program fails load", "[simctrl]") {
    DualFixture f;
    SimController ctrl(f.strips());

    CompiledProgram prog;
    prog.artifact_id = "test";
    prog.duration = 5.0f;
    prog.strips = {
        {"left", 5, f.lblob},
        {"left", 5, f.lblob},  // duplicate
    };
    CHECK_FALSE(ctrl.load(prog));
    auto evts = ctrl.drain_events();
    CHECK(has_event(evts, ControllerEvent::ERROR));
}

TEST_CASE("Strip count mismatch fails load", "[simctrl]") {
    DualFixture f;
    SimController ctrl(f.strips());

    CompiledProgram prog;
    prog.artifact_id = "test";
    prog.duration = 5.0f;
    prog.strips = {{"left", 5, f.lblob}};  // only one strip
    CHECK_FALSE(ctrl.load(prog));

    auto evts = ctrl.drain_events();
    CHECK(has_event(evts, ControllerEvent::ERROR));
}

TEST_CASE("Device load failure fails controller load", "[simctrl]") {
    DualFixture f;
    SimController ctrl(f.strips());

    auto prog = f.program();
    prog.strips[1].blob = {0xDE, 0xAD};  // garbage blob
    CHECK_FALSE(ctrl.load(prog));

    CHECK(ctrl.state() == ControllerState::IDLE);
    CHECK(ctrl.session_id() == 0);  // identity not mutated
    auto evts = ctrl.drain_events();
    CHECK(has_event(evts, ControllerEvent::ERROR));

    // Device asymmetry: strip 0 loaded OK then got handle_stop → LOADED;
    // strip 1 failed decode → IDLE. Next load() overwrites both.
    CHECK(f.left.state() == DeviceState::LOADED);
    CHECK(f.right.state() == DeviceState::IDLE);
}

TEST_CASE("Failed device load does not advance identity", "[simctrl]") {
    DualFixture f;
    SimController ctrl(f.strips());

    // Successful first load
    REQUIRE(ctrl.load(f.program()));
    CHECK(ctrl.session_id() == 1);
    CHECK(ctrl.epoch() == 0);
    ctrl.drain_events();

    // Play and tick to set epoch > 0
    f.set_time(0);
    ctrl.play();
    CHECK(ctrl.epoch() == 1);
    f.set_time(500'000);
    ctrl.tick_once();
    ctrl.drain_program_frames();
    ctrl.drain_events();

    // Failed second load (strip 1 bad blob) — strip 0 loads OK then strip 1 fails
    auto bad = f.program();
    bad.strips[1].blob = {0xDE, 0xAD};
    CHECK_FALSE(ctrl.load(bad));

    // Identity must not have advanced
    CHECK(ctrl.session_id() == 1);
    CHECK(ctrl.epoch() == 1);
    // State goes IDLE — handle_load is destructive, old program is gone
    CHECK(ctrl.state() == ControllerState::IDLE);

    // Can recover with a fresh load
    REQUIRE(ctrl.load(f.program()));
    CHECK(ctrl.session_id() == 2);
    CHECK(ctrl.state() == ControllerState::LOADED);
}

// =========================================================================
// 2. Shared-start playback
// =========================================================================

TEST_CASE("Shared-start dual-strip playback", "[simctrl]") {
    DualFixture f;
    SimController ctrl(f.strips());
    REQUIRE(ctrl.load(f.program()));
    ctrl.drain_events();

    f.set_time(0);
    ctrl.play();
    CHECK(ctrl.state() == ControllerState::PLAYING);
    CHECK(ctrl.epoch() == 1);
    ctrl.drain_events();

    // Tick at t=0.5 — paint renders
    f.set_time(500'000);
    ctrl.tick_once();
    auto pf = ctrl.drain_program_frames();
    REQUIRE(pf.size() == 1);
    CHECK(pf[0].frame_index == 0);
    CHECK(pf[0].t_rel == Catch::Approx(0.5f));
    REQUIRE(pf[0].strips.size() == 2);

    // Tick at t=1.0 — shift activates, snapshots paint
    f.set_time(1'000'000);
    ctrl.tick_once();
    pf = ctrl.drain_program_frames();
    REQUIRE(pf.size() == 1);
    CHECK(pf[0].frame_index == 1);
    CHECK(pf[0].t_rel == Catch::Approx(1.0f));

    // Left strip at t=1.0: [51, 102, 153, 204, 255]
    check_pixel_vec(pf[0].strips[0], 0, 51);
    check_pixel_vec(pf[0].strips[0], 1, 102);
    check_pixel_vec(pf[0].strips[0], 2, 153);
    check_pixel_vec(pf[0].strips[0], 3, 204);
    check_pixel_vec(pf[0].strips[0], 4, 255);

    // Right strip at t=1.0: [255, 204, 153, 102, 51]
    check_pixel_vec(pf[0].strips[1], 0, 255);
    check_pixel_vec(pf[0].strips[1], 1, 204);
    check_pixel_vec(pf[0].strips[1], 2, 153);
    check_pixel_vec(pf[0].strips[1], 3, 102);
    check_pixel_vec(pf[0].strips[1], 4, 51);

    // Tick at t=2.0 — shift has moved right by 1
    f.set_time(2'000'000);
    ctrl.tick_once();
    pf = ctrl.drain_program_frames();
    REQUIRE(pf.size() == 1);
    CHECK(pf[0].frame_index == 2);

    // Left t=2.0: [0, 51, 102, 153, 204]
    check_pixel_vec(pf[0].strips[0], 0, 0);
    check_pixel_vec(pf[0].strips[0], 1, 51);
    check_pixel_vec(pf[0].strips[0], 4, 204);

    // Right t=2.0: [0, 255, 204, 153, 102]
    check_pixel_vec(pf[0].strips[1], 0, 0);
    check_pixel_vec(pf[0].strips[1], 1, 255);
    check_pixel_vec(pf[0].strips[1], 4, 102);
}

// =========================================================================
// 3. Pause / resume
// =========================================================================

TEST_CASE("Pause and resume", "[simctrl]") {
    DualFixture f;
    SimController ctrl(f.strips());
    REQUIRE(ctrl.load(f.program()));

    f.set_time(0);
    ctrl.play();
    ctrl.drain_events();

    // Tick into program
    f.set_time(1'000'000);
    ctrl.tick_once();
    ctrl.drain_program_frames();

    // Pause
    ctrl.pause();
    CHECK(ctrl.state() == ControllerState::PAUSED);
    CHECK(ctrl.current_t_rel() == Catch::Approx(1.0f));

    auto evts = ctrl.drain_events();
    CHECK(has_state_event(evts, ControllerState::PAUSED));

    // No new frames while paused
    f.set_time(2'000'000);
    ctrl.tick_once();
    CHECK(ctrl.drain_program_frames().empty());

    // Resume
    ctrl.play();
    CHECK(ctrl.state() == ControllerState::PLAYING);
    evts = ctrl.drain_events();
    CHECK(has_state_event(evts, ControllerState::PLAYING));

    // Tick after resume — should get a frame near t=1.0 (resume position)
    ctrl.tick_once();
    auto pf = ctrl.drain_program_frames();
    REQUIRE(pf.size() >= 1);
    // Verify we're continuing from paused position, not from t=0
    CHECK(pf[0].t_rel >= 0.9f);
}

// =========================================================================
// 4. Seek
// =========================================================================

TEST_CASE("Seek from PLAYING stays PLAYING", "[simctrl]") {
    DualFixture f;
    SimController ctrl(f.strips());
    REQUIRE(ctrl.load(f.program()));

    f.set_time(0);
    ctrl.play();
    uint32_t epoch_before = ctrl.epoch();
    ctrl.drain_events();

    f.set_time(500'000);
    ctrl.tick_once();
    ctrl.drain_program_frames();

    ctrl.seek(2.0f);
    CHECK(ctrl.state() == ControllerState::PLAYING);
    CHECK(ctrl.epoch() == epoch_before + 1);

    // tick_once drains the seek frames
    ctrl.tick_once();
    auto pf = ctrl.drain_program_frames();
    REQUIRE(pf.size() >= 1);
    // Frames should be at target position
    CHECK(pf[0].t_rel == Catch::Approx(2.0f));
}

TEST_CASE("Seek from LOADED goes to PAUSED", "[simctrl]") {
    DualFixture f;
    SimController ctrl(f.strips());
    REQUIRE(ctrl.load(f.program()));
    ctrl.drain_events();

    ctrl.seek(2.0f);
    CHECK(ctrl.state() == ControllerState::PAUSED);

    // tick_once assembles the seek frame
    ctrl.tick_once();
    auto pf = ctrl.drain_program_frames();
    REQUIRE(pf.size() == 1);
    CHECK(pf[0].t_rel == Catch::Approx(2.0f));
}

TEST_CASE("Seek from PAUSED stays PAUSED", "[simctrl]") {
    DualFixture f;
    SimController ctrl(f.strips());
    REQUIRE(ctrl.load(f.program()));

    ctrl.seek(1.0f);
    CHECK(ctrl.state() == ControllerState::PAUSED);
    uint32_t epoch1 = ctrl.epoch();

    ctrl.seek(3.0f);
    CHECK(ctrl.state() == ControllerState::PAUSED);
    CHECK(ctrl.epoch() == epoch1 + 1);

    ctrl.tick_once();
    auto pf = ctrl.drain_program_frames();
    REQUIRE(pf.size() >= 1);
    // Should have frame at t=3.0
    bool found_3 = false;
    for (auto& p : pf)
        if (p.t_rel == Catch::Approx(3.0f)) found_3 = true;
    CHECK(found_3);
}

// =========================================================================
// 5. Stop
// =========================================================================

TEST_CASE("Stop and restart", "[simctrl]") {
    DualFixture f;
    SimController ctrl(f.strips());
    REQUIRE(ctrl.load(f.program()));

    f.set_time(0);
    ctrl.play();
    ctrl.drain_events();

    f.set_time(1'000'000);
    ctrl.tick_once();
    ctrl.drain_program_frames();

    ctrl.stop();
    CHECK(ctrl.state() == ControllerState::STOPPED);
    auto evts = ctrl.drain_events();
    CHECK(has_state_event(evts, ControllerState::STOPPED));

    // Device buffers should be black
    for (int i = 0; i < 5 * 3; i++) {
        CHECK(f.left.rgb_data()[i] == 0);
        CHECK(f.right.rgb_data()[i] == 0);
    }

    // Restart
    uint32_t epoch_before = ctrl.epoch();
    ctrl.play();
    CHECK(ctrl.state() == ControllerState::PLAYING);
    CHECK(ctrl.epoch() == epoch_before + 1);

    // Tick — should get a frame at t=0
    f.set_time(1'000'000);  // same wall time, but t0 was reset
    ctrl.tick_once();
    auto pf = ctrl.drain_program_frames();
    REQUIRE(pf.size() >= 1);
    CHECK(pf[0].frame_index == 0);
}

// =========================================================================
// 6. End and loop
// =========================================================================

TEST_CASE("Non-looping program ends", "[simctrl]") {
    DualFixture f;
    SimController ctrl(f.strips());
    REQUIRE(ctrl.load(f.program(false)));

    f.set_time(0);
    ctrl.play();
    ctrl.drain_events();

    // Tick past duration (5.0s)
    f.set_time(5'100'000);
    ctrl.tick_once();

    CHECK(ctrl.state() == ControllerState::ENDED);
    auto evts = ctrl.drain_events();
    CHECK(has_state_event(evts, ControllerState::ENDED));
}

TEST_CASE("Looping program restarts", "[simctrl]") {
    DualFixture f;
    SimController ctrl(f.strips());
    REQUIRE(ctrl.load(f.program(true)));

    f.set_time(0);
    ctrl.play();
    uint32_t epoch_before = ctrl.epoch();
    ctrl.drain_events();

    // Tick past duration
    f.set_time(5'100'000);
    ctrl.tick_once();

    // Should still be PLAYING, with LOOPED event
    CHECK(ctrl.state() == ControllerState::PLAYING);
    CHECK(ctrl.epoch() == epoch_before + 1);
    auto evts = ctrl.drain_events();
    CHECK(has_event(evts, ControllerEvent::LOOPED));

    // Tick after loop — should get frames from restart
    f.set_time(5'600'000);
    ctrl.tick_once();
    auto pf = ctrl.drain_program_frames();
    REQUIRE(pf.size() >= 1);
}

// =========================================================================
// 7. Gen filtering
// =========================================================================

TEST_CASE("Gen filtering across loads", "[simctrl]") {
    DualFixture f;
    SimController ctrl(f.strips());

    // First load (gen=1)
    REQUIRE(ctrl.load(f.program()));
    f.set_time(0);
    ctrl.play();
    ctrl.drain_events();
    f.set_time(500'000);
    ctrl.tick_once();
    auto pf1 = ctrl.drain_program_frames();
    REQUIRE(pf1.size() == 1);

    // Second load (gen=2) — without draining device frames from first load
    // First, tick at t=1.0 to generate more frames
    f.set_time(1'000'000);
    // Don't tick — load directly, leaving stale device frames
    REQUIRE(ctrl.load(f.program()));
    f.set_time(0);
    ctrl.play();
    ctrl.drain_events();

    f.set_time(500'000);
    ctrl.tick_once();
    auto pf2 = ctrl.drain_program_frames();
    // Should only have gen=2 frames
    REQUIRE(pf2.size() == 1);
    CHECK(pf2[0].frame_index == 0);
}

// =========================================================================
// 8. Events
// =========================================================================

TEST_CASE("Event stream on full lifecycle", "[simctrl]") {
    DualFixture f;
    SimController ctrl(f.strips());

    // Load
    REQUIRE(ctrl.load(f.program()));
    auto evts = ctrl.drain_events();
    REQUIRE(evts.size() == 2);
    CHECK(evts[0].kind == ControllerEvent::SESSION_STARTED);
    CHECK(evts[0].session_id == 1);
    CHECK(evts[1].kind == ControllerEvent::STATE_CHANGED);
    CHECK(evts[1].state == ControllerState::LOADED);

    // Play
    f.set_time(0);
    ctrl.play();
    evts = ctrl.drain_events();
    REQUIRE(evts.size() == 1);
    CHECK(evts[0].kind == ControllerEvent::STATE_CHANGED);
    CHECK(evts[0].state == ControllerState::PLAYING);
    CHECK(evts[0].epoch == 1);

    // Tick
    f.set_time(500'000);
    ctrl.tick_once();

    // Pause
    ctrl.pause();
    evts = ctrl.drain_events();
    REQUIRE(evts.size() == 1);
    CHECK(evts[0].kind == ControllerEvent::STATE_CHANGED);
    CHECK(evts[0].state == ControllerState::PAUSED);

    // Stop
    ctrl.stop();
    evts = ctrl.drain_events();
    REQUIRE(evts.size() == 1);
    CHECK(evts[0].kind == ControllerEvent::STATE_CHANGED);
    CHECK(evts[0].state == ControllerState::STOPPED);

    // Error on bad load
    CompiledProgram bad;
    bad.artifact_id = "bad";
    bad.duration = 1.0f;
    bad.strips = {{"left", 5, {0xDE}}};  // wrong count
    CHECK_FALSE(ctrl.load(bad));
    evts = ctrl.drain_events();
    REQUIRE(evts.size() == 1);
    CHECK(evts[0].kind == ControllerEvent::ERROR);
}

TEST_CASE("Seek from ENDED", "[simctrl]") {
    DualFixture f;
    SimController ctrl(f.strips());
    REQUIRE(ctrl.load(f.program(false)));

    f.set_time(0);
    ctrl.play();
    ctrl.drain_events();

    // End the program
    f.set_time(5'100'000);
    ctrl.tick_once();
    CHECK(ctrl.state() == ControllerState::ENDED);
    ctrl.drain_events();
    ctrl.drain_program_frames();

    // Seek back
    ctrl.seek(2.0f);
    CHECK(ctrl.state() == ControllerState::PAUSED);

    ctrl.tick_once();
    auto pf = ctrl.drain_program_frames();
    REQUIRE(pf.size() >= 1);
    CHECK(pf[0].t_rel == Catch::Approx(2.0f));
}

TEST_CASE("Play from ENDED restarts", "[simctrl]") {
    DualFixture f;
    SimController ctrl(f.strips());
    REQUIRE(ctrl.load(f.program(false)));

    f.set_time(0);
    ctrl.play();
    ctrl.drain_events();

    f.set_time(5'100'000);
    ctrl.tick_once();
    CHECK(ctrl.state() == ControllerState::ENDED);
    ctrl.drain_events();
    ctrl.drain_program_frames();

    // Play again from ENDED
    ctrl.play();
    CHECK(ctrl.state() == ControllerState::PLAYING);

    f.set_time(5'600'000);
    ctrl.tick_once();
    auto pf = ctrl.drain_program_frames();
    REQUIRE(pf.size() >= 1);
}
