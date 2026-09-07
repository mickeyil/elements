#include <catch2/catch_test_macros.hpp>

#include <cstdint>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

#include "core/blob_limits.h"
#include "core/decoder.h"
#include "core/engine.h"
#include "core/program.h"
#include "core/runtime_constants.h"
#include "core/strip.h"

// Cross-check that blobs emitted by the Python v4 compiler are accepted and
// run by the device decoder/engine. The fixtures are generated from the
// compiler at build time (see CMakeLists.txt), so this exercises the real
// emitter output, not a hand-built blob. FIXTURE_DIR is injected by CMake.

namespace {

std::vector<uint8_t> read_fixture(const char* name)
{
    const std::string path = std::string(FIXTURE_DIR) + "/" + name;
    std::ifstream f(path, std::ios::binary);
    REQUIRE(f.good());
    return std::vector<uint8_t>(std::istreambuf_iterator<char>(f),
                                std::istreambuf_iterator<char>());
}

Program* decode_fixture(const char* name, uint16_t profile_len, DecodeError& err)
{
    const std::vector<uint8_t> data = read_fixture(name);
    return decode_program(data.data(), data.size(), profile_len, &err);
}

// Render `t_ms` into a fresh Strip and check every pixel is the grayscale value
// `expected[i]` (S=0 means R==G==B==round(V*255)).
void check_grayscale_frame(Engine& engine, uint32_t t_ms,
                           const std::vector<uint8_t>& expected)
{
    Strip strip;
    REQUIRE(strip.resize(static_cast<uint16_t>(expected.size())));
    REQUIRE(engine.render_frame(ProgramTime{t_ms}, strip));
    for (uint16_t i = 0; i < expected.size(); i++) {
        CAPTURE(t_ms, i);
        CHECK(strip[i].r == expected[i]);
        CHECK(strip[i].g == expected[i]);
        CHECK(strip[i].b == expected[i]);
    }
}

// A frame schedule: the times one device happens to render at, in order.
using Schedule = std::vector<uint32_t>;

// What the strip must show at one of the schedule's frames.
struct Expected
{
    uint32_t t_ms;
    std::vector<rgb_t> pixels;
};

Schedule dense(uint32_t last_ms, uint32_t step_ms = 10)
{
    Schedule s;
    for (uint32_t t = 0; t <= last_ms; t += step_ms) {
        s.push_back(t);
    }
    return s;
}

rgb_t gray(uint8_t v) { return rgb_t(v, v, v); }

// Play `fixture` through a fresh engine at exactly the frames in `schedule`,
// checking every frame that has an entry in `expected`. Frames between
// entries are rendered and ignored; leaving frames out is how a schedule
// stalls or starts late.
void run_schedule(const char* fixture, uint16_t len, const Schedule& schedule,
                  const std::vector<Expected>& expected)
{
    DecodeError err = DecodeError::Ok;
    Program* prog = decode_fixture(fixture, len, err);
    REQUIRE(err == DecodeError::Ok);
    REQUIRE(prog != nullptr);
    Engine* engine = Engine::create(prog);
    REQUIRE(engine != nullptr);
    Strip strip;
    REQUIRE(strip.resize(len));

    for (uint32_t t_ms : schedule) {
        CAPTURE(fixture, t_ms);
        REQUIRE(engine->render_frame(ProgramTime{t_ms}, strip));
        for (const Expected& e : expected) {
            if (e.t_ms != t_ms) {
                continue;
            }
            REQUIRE(e.pixels.size() == len);
            for (uint16_t i = 0; i < len; i++) {
                CAPTURE(i);
                CHECK(strip[i].r == e.pixels[i].r);
                CHECK(strip[i].g == e.pixels[i].g);
                CHECK(strip[i].b == e.pixels[i].b);
            }
        }
    }
    delete engine;
}

}  // namespace

TEST_CASE("test_shift fixture decodes and plays the documented frames")
{
    DecodeError err = DecodeError::Ok;
    Program* prog = decode_fixture("test_shift.bin", 5, err);
    REQUIRE(err == DecodeError::Ok);
    REQUIRE(prog != nullptr);

    // paint[0,1) then shift[1,5) on one layer; no copy op (source intact).
    CHECK(prog->layer_count == 1);
    CHECK(prog->target_fps == 50);
    CHECK(prog->copy_ops.count() == 0);

    Engine* engine = Engine::create(prog);  // takes ownership of prog
    REQUIRE(engine != nullptr);

    // Warm up inside the paint window so its buffer holds the pattern the
    // shift snapshots at t=1000 ms.
    Strip warm;
    REQUIRE(warm.resize(5));
    REQUIRE(engine->render_frame(ProgramTime{0}, warm));

    check_grayscale_frame(*engine, 1000, {51, 102, 153, 204, 255});
    check_grayscale_frame(*engine, 2000, {0, 51, 102, 153, 204});
    check_grayscale_frame(*engine, 3000, {0, 0, 51, 102, 153});
    check_grayscale_frame(*engine, 4000, {0, 0, 0, 51, 102});

    delete engine;  // frees prog
}

TEST_CASE("test_dual_shift fixtures decode and play their documented frames")
{
    SECTION("left strip: ascending brightness")
    {
        DecodeError err = DecodeError::Ok;
        Program* prog = decode_fixture("test_dual_shift_left.bin", 5, err);
        REQUIRE(err == DecodeError::Ok);
        REQUIRE(prog != nullptr);

        Engine* engine = Engine::create(prog);
        REQUIRE(engine != nullptr);
        Strip warm;
        REQUIRE(warm.resize(5));
        REQUIRE(engine->render_frame(ProgramTime{0}, warm));

        check_grayscale_frame(*engine, 1000, {51, 102, 153, 204, 255});
        check_grayscale_frame(*engine, 2000, {0, 51, 102, 153, 204});
        check_grayscale_frame(*engine, 3000, {0, 0, 51, 102, 153});
        delete engine;
    }

    SECTION("right strip: descending brightness")
    {
        DecodeError err = DecodeError::Ok;
        Program* prog = decode_fixture("test_dual_shift_right.bin", 5, err);
        REQUIRE(err == DecodeError::Ok);
        REQUIRE(prog != nullptr);

        Engine* engine = Engine::create(prog);
        REQUIRE(engine != nullptr);
        Strip warm;
        REQUIRE(warm.resize(5));
        REQUIRE(engine->render_frame(ProgramTime{0}, warm));

        check_grayscale_frame(*engine, 1000, {255, 204, 153, 102, 51});
        check_grayscale_frame(*engine, 2000, {0, 255, 204, 153, 102});
        check_grayscale_frame(*engine, 3000, {0, 0, 255, 204, 153});
        delete engine;
    }
}

TEST_CASE("test_animation fixture decodes to the expected structure and renders")
{
    DecodeError err = DecodeError::Ok;
    Program* prog = decode_fixture("test_animation.bin", 10, err);
    REQUIRE(err == DecodeError::Ok);
    REQUIRE(prog != nullptr);

    // wave+shift on layer 0, eight sparks on layer 1; shift reads the wave
    // buffer in place, so no copy op.
    CHECK(prog->layer_count == 2);
    CHECK(prog->target_fps == 50);
    CHECK(prog->requires_sync == false);
    CHECK(prog->duration.ms == 2000);
    CHECK(prog->copy_ops.count() == 0);
    REQUIRE(prog->layers != nullptr);
    CHECK(prog->layers[0].count() == 2);
    CHECK(prog->layers[1].count() == 8);

    // Render across both the wave and shift windows; just assert it runs.
    Engine* engine = Engine::create(prog);
    REQUIRE(engine != nullptr);
    Strip strip;
    REQUIRE(strip.resize(10));
    for (uint32_t t_ms : {0u, 500u, 1000u, 1500u, 1900u}) {
        CAPTURE(t_ms);
        CHECK(engine->render_frame(ProgramTime{t_ms}, strip));
    }
    delete engine;
}

TEST_CASE("test_full_paint fixture: per-pixel paint spans a full strip")
{
    // Proves a per-pixel paint with > 255 colors (u16 count) decodes and runs.
    DecodeError err = DecodeError::Ok;
    Program* prog = decode_fixture("test_full_paint.bin", MAX_STRIP_PIXELS, err);
    REQUIRE(err == DecodeError::Ok);
    REQUIRE(prog != nullptr);

    CHECK(prog->layer_count == 1);
    REQUIRE(prog->layers != nullptr);
    CHECK(prog->layers[0].count() == 1);

    Engine* engine = Engine::create(prog);
    REQUIRE(engine != nullptr);
    Strip strip;
    REQUIRE(strip.resize(MAX_STRIP_PIXELS));
    REQUIRE(engine->render_frame(ProgramTime{0}, strip));
    // Pixel 0 is hue 0, S=1, V=1 -> red.
    CHECK(strip[0].r == 255);
    CHECK(strip[0].g == 0);
    CHECK(strip[0].b == 0);
    delete engine;
}

TEST_CASE("fixture strip-length mismatch is rejected")
{
    // The decoder must reject a blob whose strip_length != the active profile.
    DecodeError err = DecodeError::Ok;
    Program* prog = decode_fixture("test_shift.bin", 7, err);
    CHECK(err == DecodeError::StripLengthMismatch);
    CHECK(prog == nullptr);
}

TEST_CASE("test_pacifica fixture decodes and renders non-black ocean frames")
{
    DecodeError err = DecodeError::Ok;
    Program* prog = decode_fixture("test_pacifica.bin", 10, err);
    REQUIRE(err == DecodeError::Ok);
    REQUIRE(prog != nullptr);

    CHECK(prog->layer_count == 1);
    CHECK(prog->duration.ms == 4000);
    REQUIRE(prog->layers != nullptr);
    CHECK(prog->layers[0].count() == 1);

    Engine* engine = Engine::create(prog);
    REQUIRE(engine != nullptr);
    Strip strip;
    REQUIRE(strip.resize(10));
    for (uint32_t t_ms : {0u, 1000u, 2500u, 3900u}) {
        CAPTURE(t_ms);
        REQUIRE(engine->render_frame(ProgramTime{t_ms}, strip));
        // The deepen step floors every pixel above pure black, so a frame of
        // zeros would mean the scratch was never composited.
        bool any_lit = false;
        for (uint16_t i = 0; i < 10; i++) {
            if (strip[i].r || strip[i].g || strip[i].b) any_lit = true;
        }
        CHECK(any_lit);
    }
    delete engine;
}

TEST_CASE("test_decimal_timeline fixture: decimal boundaries meet on the ms grid")
{
    // Beat 0.3 s: paint A [0,300), paint B [300,600) on one layer, then a
    // shift [600,900) sourced from A, so a preserve copy lands at 300. Every
    // boundary here failed the float32 start+duration checks in blob v3.
    DecodeError err = DecodeError::Ok;
    Program* prog = decode_fixture("test_decimal_timeline.bin", 4, err);
    REQUIRE(err == DecodeError::Ok);
    REQUIRE(prog != nullptr);

    CHECK(prog->duration.ms == 900);
    CHECK(prog->layer_count == 1);
    REQUIRE(prog->layers != nullptr);
    REQUIRE(prog->layers[0].count() == 3);
    const Layer& layer = prog->layers[0];
    CHECK(layer.at(0).start.ms == 0);
    CHECK((layer.at(0).start + layer.at(0).duration).ms == 300);
    CHECK(layer.at(1).start.ms == 300);
    CHECK((layer.at(1).start + layer.at(1).duration).ms == 600);
    CHECK(layer.at(2).start.ms == 600);
    CHECK((layer.at(2).start + layer.at(2).duration).ms == 900);
    REQUIRE(prog->copy_ops.count() == 1);
    CHECK(prog->copy_ops.at(0).at.ms == 300);

    Engine* engine = Engine::create(prog);
    REQUIRE(engine != nullptr);
    check_grayscale_frame(*engine, 0,   {102, 102, 102, 102});
    check_grayscale_frame(*engine, 299, {102, 102, 102, 102});
    check_grayscale_frame(*engine, 300, {204, 204, 204, 204});
    check_grayscale_frame(*engine, 599, {204, 204, 204, 204});
    check_grayscale_frame(*engine, 600, {102, 102, 102, 102});
    check_grayscale_frame(*engine, 899, {102, 102, 102, 102});
    delete engine;
}

// ---------------------------------------------------------------------------
// Source handoffs under different frame schedules (generate_handoff.py)
// ---------------------------------------------------------------------------

TEST_CASE("handoff fixtures: a source no frame rendered still feeds the shift")
{
    // White paint [120,180); the shift holds it over [200,600).
    const std::vector<Expected> direct = {
        {0, {gray(0)}}, {150, {gray(255)}}, {300, {gray(255)}},
        {400, {gray(255)}}, {600, {gray(0)}},
    };
    SECTION("direct read, dense frames") {
        run_schedule("test_handoff_skipped_direct.bin", 1, dense(600), direct);
    }
    SECTION("direct read, 5 fps skips the paint") {
        run_schedule("test_handoff_skipped_direct.bin", 1, {0, 200, 400, 600}, direct);
    }
    SECTION("direct read, late first frame") {
        run_schedule("test_handoff_skipped_direct.bin", 1, {300}, direct);
    }
    SECTION("direct read, stall over the paint") {
        run_schedule("test_handoff_skipped_direct.bin", 1, {0, 100, 400, 600}, direct);
    }

    // A dim paint [180,200) overwrites the buffer, so a copy op at 180
    // preserves white for the shift.
    const std::vector<Expected> copied = {
        {0, {gray(0)}}, {150, {gray(255)}}, {190, {gray(102)}},
        {300, {gray(255)}}, {400, {gray(255)}}, {600, {gray(0)}},
    };
    SECTION("preserve copy, dense frames") {
        run_schedule("test_handoff_skipped_copy.bin", 1, dense(600), copied);
    }
    SECTION("preserve copy, 5 fps skips both paints") {
        run_schedule("test_handoff_skipped_copy.bin", 1, {0, 200, 400, 600}, copied);
    }
    SECTION("preserve copy, late first frame") {
        run_schedule("test_handoff_skipped_copy.bin", 1, {300}, copied);
    }
    SECTION("preserve copy, stall over both paints") {
        run_schedule("test_handoff_skipped_copy.bin", 1, {0, 100, 400, 600}, copied);
    }
}

TEST_CASE("handoff fixtures: the shift captures the wave's value at its end")
{
    // The wave runs 255 -> 0 -> 255 over [0,400); the shift holds 255.
    const std::vector<Expected> expected = {
        {0, {gray(255)}}, {200, {gray(0)}},
        {500, {gray(255)}}, {600, {gray(255)}}, {700, {gray(255)}},
    };
    SECTION("dense frames") {
        run_schedule("test_handoff_wave_endpoint.bin", 1, dense(700), expected);
    }
    SECTION("5 fps on the grid") {
        run_schedule("test_handoff_wave_endpoint.bin", 1, {0, 200, 400, 600}, expected);
    }
    SECTION("5 fps offset by 100 ms") {
        run_schedule("test_handoff_wave_endpoint.bin", 1, {100, 300, 500, 700}, expected);
    }
    SECTION("late first frame") {
        run_schedule("test_handoff_wave_endpoint.bin", 1, {600}, expected);
    }
}

TEST_CASE("handoff fixtures: a stall across a moving shift chain rebuilds every link")
{
    // Ramp [0,100); shift A moves it 1 px per 100 ms over [100,300); shift B
    // holds A's endpoint on pixels 1-2 over [300,600).
    const std::vector<Expected> expected = {
        {0,   {gray(51), gray(102), gray(153), gray(204)}},
        {200, {gray(204), gray(51), gray(102), gray(153)}},
        {450, {gray(0), gray(204), gray(51), gray(0)}},
        {550, {gray(0), gray(204), gray(51), gray(0)}},
    };
    SECTION("dense frames") {
        run_schedule("test_handoff_shift_chain.bin", 4, dense(550), expected);
    }
    SECTION("one stall across both handoffs") {
        run_schedule("test_handoff_shift_chain.bin", 4, {0, 450}, expected);
    }
    SECTION("late first frame") {
        run_schedule("test_handoff_shift_chain.bin", 4, {450}, expected);
    }
    SECTION("sparse frames off every boundary") {
        run_schedule("test_handoff_shift_chain.bin", 4, {50, 250, 350, 550}, expected);
    }
}

TEST_CASE("handoff fixtures: a shift with no source= holds its layer's previous event")
{
    // White paint [0,100); the shift reads its own layer buffer over [100,300).
    const std::vector<Expected> expected = {
        {0, {gray(255)}}, {150, {gray(255)}}, {200, {gray(255)}}, {300, {gray(0)}},
    };
    SECTION("dense frames") {
        run_schedule("test_handoff_self_source.bin", 1, dense(300), expected);
    }
    SECTION("late first frame") {
        run_schedule("test_handoff_self_source.bin", 1, {200}, expected);
    }
    SECTION("stall over the paint") {
        run_schedule("test_handoff_self_source.bin", 1, {0, 200, 300}, expected);
    }
    SECTION("reset, then a late first frame") {
        DecodeError err = DecodeError::Ok;
        Program* prog = decode_fixture("test_handoff_self_source.bin", 1, err);
        REQUIRE(err == DecodeError::Ok);
        Engine* engine = Engine::create(prog);
        REQUIRE(engine != nullptr);
        Strip strip;
        REQUIRE(strip.resize(1));

        REQUIRE(engine->render_frame(ProgramTime{250}, strip));
        engine->reset();
        REQUIRE(engine->render_frame(ProgramTime{200}, strip));
        CHECK(strip[0].r == 255);
        CHECK(strip[0].g == 255);
        CHECK(strip[0].b == 255);
        delete engine;
    }
}

TEST_CASE("handoff fixtures: a shift snapshots before a later copy reuses its preserve slot")
{
    // L0: red, blue, then shift 1 holding red from 200. L1: green, yellow,
    // then shift 2 holding green over [400,600). Both preserve copies share
    // one pool slot; shift 1 must have taken red before copy 2 lands at 300.
    const rgb_t red(255, 0, 0);
    const rgb_t green(0, 255, 0);
    const rgb_t yellow(255, 255, 0);
    const std::vector<Expected> expected = {
        {350, {yellow}}, {450, {green}}, {650, {red}},
    };
    SECTION("the fixture really shares one slot between both preserve copies") {
        DecodeError err = DecodeError::Ok;
        Program* prog = decode_fixture("test_handoff_slot_reuse.bin", 1, err);
        REQUIRE(err == DecodeError::Ok);
        REQUIRE(prog->copy_ops.count() == 2);
        const CopyOp& first = prog->copy_ops.at(0);
        const CopyOp& second = prog->copy_ops.at(1);
        CHECK(first.at.ms == 100);
        CHECK(second.at.ms == 300);
        CHECK(&prog->pixel_views.at(first.dst_pixv_idx)[0]
              == &prog->pixel_views.at(second.dst_pixv_idx)[0]);
        free_program(prog);
    }
    SECTION("dense frames") {
        run_schedule("test_handoff_slot_reuse.bin", 1, dense(650), expected);
    }
    SECTION("stall from 150 to 350") {
        run_schedule("test_handoff_slot_reuse.bin", 1, {0, 150, 350, 450, 650}, expected);
    }
    SECTION("late first frame") {
        run_schedule("test_handoff_slot_reuse.bin", 1, {650}, expected);
    }
}
