#include <catch2/catch_test_macros.hpp>
#include <catch2/catch_approx.hpp>
#include "../src/decoder.h"

#include <cstdio>
#include <cstdlib>
#include <cmath>
#include <vector>

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

// ---------------------------------------------------------------------------
// Fixture: decode once
// ---------------------------------------------------------------------------

static Program* get_test_program() {
    static Program* prog = nullptr;
    if (!prog) {
        auto blob = load_blob("test/fixtures/test_animation.bin");
        prog = decode_program(blob.data(), blob.size());
        REQUIRE(prog != nullptr);
    }
    return prog;
}

// ---------------------------------------------------------------------------
// Header tests
// ---------------------------------------------------------------------------

TEST_CASE("Header fields", "[header]") {
    Program* p = get_test_program();

    CHECK(p->layer_count == 2);
    CHECK(p->pool.count == 1);
    CHECK(p->max_remap_length == 10);
    CHECK(Catch::Approx(p->duration).epsilon(1e-6) == 2.0f);
}

// ---------------------------------------------------------------------------
// Layer structure
// ---------------------------------------------------------------------------

TEST_CASE("Layer 0 structure", "[layers]") {
    Program* p = get_test_program();
    LayerDef& l0 = p->layers[0];

    SECTION("index map") {
        REQUIRE(l0.index_map_length == 10);
        for (uint8_t i = 0; i < 10; i++) {
            CHECK(l0.index_map[i] == i);
        }
    }

    SECTION("event count") {
        CHECK(l0.event_count == 2);
    }

    SECTION("event types") {
        CHECK(l0.events[0].params.type == ANIM_WAVE);
        CHECK(l0.events[1].params.type == ANIM_SHIFT);
    }
}

TEST_CASE("Layer 1 structure", "[layers]") {
    Program* p = get_test_program();
    LayerDef& l1 = p->layers[1];

    SECTION("merged index map") {
        REQUIRE(l1.index_map_length == 4);
        CHECK(l1.index_map[0] == 0);
        CHECK(l1.index_map[1] == 4);
        CHECK(l1.index_map[2] == 5);
        CHECK(l1.index_map[3] == 9);
    }

    SECTION("event count") {
        CHECK(l1.event_count == 8);
    }

    SECTION("all sparks") {
        for (uint16_t i = 0; i < l1.event_count; i++) {
            CHECK(l1.events[i].params.type == ANIM_SPARK);
        }
    }
}

// ---------------------------------------------------------------------------
// Time resolution
// ---------------------------------------------------------------------------

TEST_CASE("Wave timing", "[timing]") {
    Program* p = get_test_program();
    auto& wave = p->layers[0].events[0];

    CHECK(Catch::Approx(wave.t_start).epsilon(1e-6) == 0.0f);
    CHECK(Catch::Approx(wave.duration).epsilon(1e-6) == 1.0f);
}

TEST_CASE("Shift timing", "[timing]") {
    Program* p = get_test_program();
    auto& shift = p->layers[0].events[1];

    CHECK(Catch::Approx(shift.t_start).epsilon(1e-6) == 1.0f);
    CHECK(Catch::Approx(shift.duration).epsilon(1e-6) == 1.0f);
}

TEST_CASE("Spark timing", "[timing]") {
    Program* p = get_test_program();
    auto& first_spark = p->layers[1].events[0];

    // First spark: at=0.0s, duration=0.1s (sec(0.1) with beat=0.5)
    CHECK(Catch::Approx(first_spark.t_start).epsilon(1e-6) == 0.0f);
    CHECK(Catch::Approx(first_spark.duration).epsilon(1e-6) == 0.1f);
}

// ---------------------------------------------------------------------------
// Remap
// ---------------------------------------------------------------------------

TEST_CASE("Wave remap is identity", "[remap]") {
    Program* p = get_test_program();
    auto& wave = p->layers[0].events[0];

    CHECK(wave.remap_is_identity == true);
    CHECK(wave.remap_length == 10);
    for (uint8_t i = 0; i < 10; i++) {
        CHECK(wave.remap[i] == i);
    }
}

TEST_CASE("Shift remap is identity", "[remap]") {
    Program* p = get_test_program();
    auto& shift = p->layers[0].events[1];

    CHECK(shift.remap_is_identity == true);
    CHECK(shift.remap_length == 10);
}

TEST_CASE("Spark white remap", "[remap]") {
    Program* p = get_test_program();
    // White sparks have color_h ≈ 0
    for (uint16_t i = 0; i < p->layers[1].event_count; i++) {
        auto& e = p->layers[1].events[i];
        if (std::abs(e.params.spark.color_h) < 1e-6) {
            CHECK(e.remap_is_identity == false);
            REQUIRE(e.remap_length == 2);
            CHECK(e.remap[0] == 0);
            CHECK(e.remap[1] == 1);
        }
    }
}

TEST_CASE("Spark yellow remap", "[remap]") {
    Program* p = get_test_program();
    // Yellow sparks have color_h ≈ 60
    for (uint16_t i = 0; i < p->layers[1].event_count; i++) {
        auto& e = p->layers[1].events[i];
        if (std::abs(e.params.spark.color_h - 60.0f) < 1e-6) {
            CHECK(e.remap_is_identity == false);
            REQUIRE(e.remap_length == 2);
            CHECK(e.remap[0] == 2);
            CHECK(e.remap[1] == 3);
        }
    }
}

// ---------------------------------------------------------------------------
// Animation params
// ---------------------------------------------------------------------------

TEST_CASE("Wave params", "[params]") {
    Program* p = get_test_program();
    auto& wp = p->layers[0].events[0].params.wave;

    CHECK(wp.channel == 2);  // V
    CHECK(Catch::Approx(wp.h).epsilon(1e-6) == 220.0f);
    CHECK(Catch::Approx(wp.s).epsilon(1e-6) == 1.0f);
    CHECK(Catch::Approx(wp.min_val).epsilon(1e-6) == 0.0f);
    CHECK(Catch::Approx(wp.max_val).epsilon(1e-6) == 0.4f);
    // period=8 beats * 0.5 = 4.0s
    CHECK(Catch::Approx(wp.period).epsilon(1e-6) == 4.0f);
    CHECK(Catch::Approx(wp.pixel_step).epsilon(1e-4) == (float)M_PI);
}

TEST_CASE("Shift params", "[params]") {
    Program* p = get_test_program();
    auto& sp = p->layers[0].events[1].params.shift;

    CHECK(sp.direction == 1);  // right
    // velocity=2 pixels/beat → 4.0 pixels/sec
    CHECK(Catch::Approx(sp.velocity).epsilon(1e-6) == 4.0f);
    CHECK(sp.circular == 0);
    CHECK(sp.init_mode == 1);    // SNAPSHOT
    CHECK(sp.source_layer == 0);
    CHECK(sp.buffer_id == 0);
    // fill = transparent → H=0, S=0, V=0, A=0
    CHECK(Catch::Approx(sp.fill_a).epsilon(1e-6) == 0.0f);
}

TEST_CASE("Spark params", "[params]") {
    Program* p = get_test_program();
    auto& first = p->layers[1].events[0].params.spark;

    // fade=0.1 beats * 0.5 = 0.05s
    CHECK(Catch::Approx(first.fade).epsilon(1e-6) == 0.05f);
}

// ---------------------------------------------------------------------------
// Buffer pool
// ---------------------------------------------------------------------------

TEST_CASE("Buffer pool", "[buffers]") {
    Program* p = get_test_program();

    CHECK(p->pool.count == 1);
    CHECK(p->pool.sizes[0] == 10);
    CHECK(p->pool.buffers[0] != nullptr);
}

TEST_CASE("Temp buffer allocated", "[buffers]") {
    Program* p = get_test_program();
    CHECK(p->temp_buffer != nullptr);
}

// ---------------------------------------------------------------------------
// Invalid blobs
// ---------------------------------------------------------------------------

TEST_CASE("Null blob returns nullptr", "[invalid]") {
    CHECK(decode_program(nullptr, 0) == nullptr);
}

TEST_CASE("Too short blob returns nullptr", "[invalid]") {
    uint8_t blob[] = {'E', 'L', 'E', 'M', 1};
    CHECK(decode_program(blob, sizeof(blob)) == nullptr);
}

TEST_CASE("Wrong magic returns nullptr", "[invalid]") {
    uint8_t blob[12] = {0};
    blob[0] = 'X';
    CHECK(decode_program(blob, sizeof(blob)) == nullptr);
}

TEST_CASE("Wrong version returns nullptr", "[invalid]") {
    uint8_t blob[12] = {'E', 'L', 'E', 'M', 99, 0, 0, 0, 0, 0, 0, 0};
    CHECK(decode_program(blob, sizeof(blob)) == nullptr);
}

// ---------------------------------------------------------------------------
// Free doesn't crash
// ---------------------------------------------------------------------------

TEST_CASE("free_program on valid program", "[free]") {
    auto blob_data = load_blob("test/fixtures/test_animation.bin");
    Program* p = decode_program(blob_data.data(), blob_data.size());
    REQUIRE(p != nullptr);
    free_program(p);  // should not crash or leak
}

TEST_CASE("free_program on nullptr", "[free]") {
    free_program(nullptr);  // should not crash
}
