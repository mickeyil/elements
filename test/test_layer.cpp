#include <catch2/catch_test_macros.hpp>

#include <cstdint>

#include "core/animation.h"
#include "core/layer.h"
#include "core/runtime_constants.h"

namespace {

// Minimal Animation stand-in. Layer only owns and frees the instance; it never
// calls render(), so the body can be a no-op. A counter lets tests verify that
// reset/destruction frees every animation exactly once.
class FakeAnim : public Animation {
public:
    explicit FakeAnim(int* live_counter) : _live(live_counter) { ++*_live; }
    ~FakeAnim() override { --*_live; }
    void render(PixelView&, float) override {}
private:
    int* _live;
};

AnimationEvent make_event(uint32_t start_ms, uint32_t duration_ms, Animation* anim,
                          uint16_t dst = 0) {
    AnimationEvent e;
    e.animation = anim;
    e.start = ProgramTime{start_ms};
    e.duration = ProgramDuration{duration_ms};
    e.dst_pixv_idx = dst;
    return e;
}

// Build a heap-allocated event array with `count` events; caller owns until
// passed to Layer::initialize().
AnimationEvent* make_events(int count, int* live_counter,
                            const uint32_t* starts, const uint32_t* durations) {
    AnimationEvent* arr = new AnimationEvent[count];
    for (int i = 0; i < count; i++) {
        arr[i] = make_event(starts[i], durations[i], new FakeAnim(live_counter));
    }
    return arr;
}

}  // namespace

// ---------------------------------------------------------------------------
// Default / empty
// ---------------------------------------------------------------------------

TEST_CASE("Layer: default-constructed is empty", "[layer]") {
    Layer layer;
    CHECK(layer.count() == 0);
    CHECK(layer.active_at(ProgramTime{0}) == nullptr);
}

TEST_CASE("Layer: active_at on empty layer returns nullptr", "[layer]") {
    Layer layer;
    CHECK(layer.active_at(ProgramTime{0}) == nullptr);
    CHECK(layer.active_at(ProgramTime{100000}) == nullptr);
}

// ---------------------------------------------------------------------------
// initialize / reset / ownership
// ---------------------------------------------------------------------------

TEST_CASE("Layer: initialize adopts the event array", "[layer]") {
    int live = 0;
    const uint32_t starts[]    = { 0, 1000 };
    const uint32_t durations[] = { 500, 500 };
    AnimationEvent* events = make_events(2, &live, starts, durations);
    REQUIRE(live == 2);

    Layer layer;
    layer.initialize(events, 2);

    CHECK(layer.count() == 2);
    CHECK(layer.at(0).start.ms == 0);
    CHECK(layer.at(1).start.ms == 1000);
    CHECK(live == 2);
}

TEST_CASE("Layer: reset frees the array and every animation", "[layer]") {
    int live = 0;
    const uint32_t starts[]    = { 0, 1000, 2000 };
    const uint32_t durations[] = { 500, 500, 500 };
    Layer layer;
    layer.initialize(make_events(3, &live, starts, durations), 3);
    REQUIRE(live == 3);

    layer.reset();

    CHECK(layer.count() == 0);
    CHECK(live == 0);
    CHECK(layer.active_at(ProgramTime{250}) == nullptr);
}

TEST_CASE("Layer: destructor frees the array and every animation", "[layer]") {
    int live = 0;
    const uint32_t starts[]    = { 0, 1000 };
    const uint32_t durations[] = { 500, 500 };
    {
        Layer layer;
        layer.initialize(make_events(2, &live, starts, durations), 2);
        REQUIRE(live == 2);
    }
    CHECK(live == 0);
}

TEST_CASE("Layer: re-initialize replaces the previous events", "[layer]") {
    int live = 0;
    const uint32_t starts1[]    = { 0 };
    const uint32_t durations1[] = { 1000 };
    const uint32_t starts2[]    = { 0, 2000 };
    const uint32_t durations2[] = { 1000, 1000 };

    Layer layer;
    layer.initialize(make_events(1, &live, starts1, durations1), 1);
    REQUIRE(live == 1);

    layer.initialize(make_events(2, &live, starts2, durations2), 2);

    CHECK(layer.count() == 2);
    CHECK(live == 2);  // first batch freed; second batch alive.
}

// ---------------------------------------------------------------------------
// active_at
// ---------------------------------------------------------------------------

TEST_CASE("Layer: active_at returns the covering event", "[layer]") {
    int live = 0;
    const uint32_t starts[]    = { 1000, 5000, 10000 };
    const uint32_t durations[] = { 2000, 1000, 3000 };  // [1,3) [5,6) [10,13)
    Layer layer;
    layer.initialize(make_events(3, &live, starts, durations), 3);

    const AnimationEvent* e0 = &layer.at(0);
    const AnimationEvent* e1 = &layer.at(1);
    const AnimationEvent* e2 = &layer.at(2);

    SECTION("before first event") {
        CHECK(layer.active_at(ProgramTime{0}) == nullptr);
        CHECK(layer.active_at(ProgramTime{999}) == nullptr);
    }
    SECTION("on event start (inclusive)") {
        CHECK(layer.active_at(ProgramTime{1000}) == e0);
        CHECK(layer.active_at(ProgramTime{5000}) == e1);
        CHECK(layer.active_at(ProgramTime{10000}) == e2);
    }
    SECTION("inside event") {
        CHECK(layer.active_at(ProgramTime{2000}) == e0);
        CHECK(layer.active_at(ProgramTime{5500}) == e1);
        CHECK(layer.active_at(ProgramTime{11500}) == e2);
    }
    SECTION("on event end (exclusive)") {
        CHECK(layer.active_at(ProgramTime{3000}) == nullptr);
        CHECK(layer.active_at(ProgramTime{6000}) == nullptr);
        CHECK(layer.active_at(ProgramTime{13000}) == nullptr);
    }
    SECTION("inside gap between events") {
        CHECK(layer.active_at(ProgramTime{3500}) == nullptr);
        CHECK(layer.active_at(ProgramTime{8000}) == nullptr);
    }
    SECTION("past last event") {
        CHECK(layer.active_at(ProgramTime{20000}) == nullptr);
    }
}

TEST_CASE("Layer: active_at on a back-to-back schedule (no gaps)", "[layer]") {
    int live = 0;
    const uint32_t starts[]    = { 0, 1000, 2000 };
    const uint32_t durations[] = { 1000, 1000, 1000 };  // [0,1) [1,2) [2,3)
    Layer layer;
    layer.initialize(make_events(3, &live, starts, durations), 3);

    CHECK(layer.active_at(ProgramTime{0}) == &layer.at(0));
    CHECK(layer.active_at(ProgramTime{999}) == &layer.at(0));
    CHECK(layer.active_at(ProgramTime{1000}) == &layer.at(1));
    CHECK(layer.active_at(ProgramTime{2000}) == &layer.at(2));
    CHECK(layer.active_at(ProgramTime{3000}) == nullptr);
}
