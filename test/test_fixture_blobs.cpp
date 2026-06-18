#include <catch2/catch_test_macros.hpp>

#include <cstdint>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

#include "../src/decoder.h"
#include "../src/engine.h"
#include "../src/program.h"
#include "../src/runtime_constants.h"
#include "../src/strip.h"

// Cross-check that blobs emitted by the Python v3 compiler are accepted and
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

// Render `t` into a fresh Strip and check every pixel is the grayscale value
// `expected[i]` (S=0 means R==G==B==round(V*255)).
void check_grayscale_frame(Engine& engine, float t,
                           const std::vector<uint8_t>& expected)
{
    Strip strip;
    REQUIRE(strip.resize(static_cast<uint16_t>(expected.size())));
    REQUIRE(engine.render_frame(t, strip));
    for (uint16_t i = 0; i < expected.size(); i++) {
        CAPTURE(t, i);
        CHECK(strip[i].r == expected[i]);
        CHECK(strip[i].g == expected[i]);
        CHECK(strip[i].b == expected[i]);
    }
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
    // shift snapshots at t=1.0.
    Strip warm;
    REQUIRE(warm.resize(5));
    REQUIRE(engine->render_frame(0.0f, warm));

    check_grayscale_frame(*engine, 1.0f, {51, 102, 153, 204, 255});
    check_grayscale_frame(*engine, 2.0f, {0, 51, 102, 153, 204});
    check_grayscale_frame(*engine, 3.0f, {0, 0, 51, 102, 153});
    check_grayscale_frame(*engine, 4.0f, {0, 0, 0, 51, 102});

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
        REQUIRE(engine->render_frame(0.0f, warm));

        check_grayscale_frame(*engine, 1.0f, {51, 102, 153, 204, 255});
        check_grayscale_frame(*engine, 2.0f, {0, 51, 102, 153, 204});
        check_grayscale_frame(*engine, 3.0f, {0, 0, 51, 102, 153});
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
        REQUIRE(engine->render_frame(0.0f, warm));

        check_grayscale_frame(*engine, 1.0f, {255, 204, 153, 102, 51});
        check_grayscale_frame(*engine, 2.0f, {0, 255, 204, 153, 102});
        check_grayscale_frame(*engine, 3.0f, {0, 0, 255, 204, 153});
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
    CHECK(prog->duration == 2.0f);
    CHECK(prog->copy_ops.count() == 0);
    REQUIRE(prog->layers != nullptr);
    CHECK(prog->layers[0].count() == 2);
    CHECK(prog->layers[1].count() == 8);

    // Render across both the wave and shift windows; just assert it runs.
    Engine* engine = Engine::create(prog);
    REQUIRE(engine != nullptr);
    Strip strip;
    REQUIRE(strip.resize(10));
    for (float t : {0.0f, 0.5f, 1.0f, 1.5f, 1.9f}) {
        CAPTURE(t);
        CHECK(engine->render_frame(t, strip));
    }
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
