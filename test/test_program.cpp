#include <catch2/catch_test_macros.hpp>

#include <cstdint>

#include "core/animation.h"
#include "core/copy_ops.h"
#include "core/layer.h"
#include "core/pixel_buffer_pool.h"
#include "core/pixel_views.h"
#include "core/program.h"
#include "core/runtime_constants.h"

namespace {

// Live-instance counter so ownership tests can prove every animation gets
// freed exactly once when Program tears down.
class FakeAnim : public Animation {
public:
    explicit FakeAnim(int* live) : _live(live) { ++*_live; }
    ~FakeAnim() override { --*_live; }
    void render(PixelView&, float) override {}
private:
    int* _live;
};

// Build a heap Program with `layer_count` layers of `events_per_layer` events,
// each event holding a fresh FakeAnim that bumps `live`.
Program* make_populated_program(uint8_t layer_count, uint16_t events_per_layer,
                                int* live) {
    Program* p = new Program();
    p->duration = ProgramDuration{10000};
    p->layer_count = layer_count;
    p->layers = new Layer[layer_count];

    for (uint8_t li = 0; li < layer_count; li++) {
        AnimationEvent* events = new AnimationEvent[events_per_layer];
        for (uint16_t ei = 0; ei < events_per_layer; ei++) {
            events[ei].animation = new FakeAnim(live);
            events[ei].start = ProgramTime{static_cast<uint32_t>(ei) * 1000u};
            events[ei].duration = ProgramDuration{500};
        }
        p->layers[li].initialize(events, events_per_layer);
    }
    return p;
}

}  // namespace

// ---------------------------------------------------------------------------
// Default state
// ---------------------------------------------------------------------------

TEST_CASE("Program: default-constructed is empty", "[program]") {
    Program p;
    CHECK(p.duration.ms == 0);
    CHECK(p.target_fps == 50);
    CHECK(p.requires_sync == false);
    CHECK(p.layer_count == 0);
    CHECK(p.layers == nullptr);
    CHECK(p.copy_ops.count() == 0);
    CHECK(p.pixel_views.count() == 0);
    CHECK(p.pixel_buffer_pool.buffer_count() == 0);
}

TEST_CASE("Program: default destructor leaves the empty state alone", "[program]") {
    {
        Program p;
        // No allocations; dtor must not double-free or crash.
    }
    SUCCEED();
}

// ---------------------------------------------------------------------------
// Layer / event / animation ownership
// ---------------------------------------------------------------------------

TEST_CASE("Program: destructor releases layers, events, and animations",
          "[program]") {
    int live = 0;
    {
        Program* p = make_populated_program(/*layers*/ 2, /*events*/ 3, &live);
        REQUIRE(live == 6);
        delete p;
    }
    CHECK(live == 0);
}

TEST_CASE("Program: free_program releases the same chain as delete", "[program]") {
    int live = 0;
    Program* p = make_populated_program(/*layers*/ 3, /*events*/ 2, &live);
    REQUIRE(live == 6);

    free_program(p);

    CHECK(live == 0);
}

TEST_CASE("Program: free_program(nullptr) is a no-op", "[program]") {
    free_program(nullptr);  // must not crash
    SUCCEED();
}

// ---------------------------------------------------------------------------
// Embedded container teardown
// ---------------------------------------------------------------------------

TEST_CASE("Program: embedded pool / views / copy_ops tear down cleanly",
          "[program]") {
    Program p;

    const uint16_t buffer_sizes[] = { 4 };
    REQUIRE(p.pixel_buffer_pool.initialize(buffer_sizes, 1));

    PixelViewSpec spec;
    spec.buffer_idx = 0;
    spec.size = 4;
    spec.storage_identity = true;
    spec.has_physical_mapping = true;
    spec.physical_identity = true;
    REQUIRE(p.pixel_views.initialize(p.pixel_buffer_pool, &spec, 1));

    CopyOp op;
    op.at = ProgramTime{500};
    op.src_pixv_idx = 0;
    op.dst_pixv_idx = 0;
    REQUIRE(p.copy_ops.initialize(&op, 1));

    CHECK(p.pixel_buffer_pool.buffer_count() == 1);
    CHECK(p.pixel_views.count() == 1);
    CHECK(p.copy_ops.count() == 1);
    // Dtor runs at scope end; valgrind verifies no leaks.
}

TEST_CASE("Program: full Program (layers + embedded containers) destructs cleanly",
          "[program]") {
    int live = 0;
    {
        Program* p = make_populated_program(/*layers*/ 2, /*events*/ 2, &live);
        REQUIRE(live == 4);

        const uint16_t sizes[] = { 4 };
        REQUIRE(p->pixel_buffer_pool.initialize(sizes, 1));
        PixelViewSpec spec;
        spec.buffer_idx = 0;
        spec.size = 4;
        spec.storage_identity = true;
        spec.has_physical_mapping = true;
        spec.physical_identity = true;
        REQUIRE(p->pixel_views.initialize(p->pixel_buffer_pool, &spec, 1));
        CopyOp op = { ProgramTime{0}, 0, 0 };
        REQUIRE(p->copy_ops.initialize(&op, 1));

        free_program(p);
    }
    CHECK(live == 0);
}
