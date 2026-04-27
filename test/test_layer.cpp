#include <catch2/catch_test_macros.hpp>

#include <cstdint>

#include "../src/animation.h"
#include "../src/layer.h"
#include "../src/runtime_constants.h"

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

AnimationEvent make_event(float start, float duration, Animation* anim,
                          uint16_t dst = 0) {
    AnimationEvent e;
    e.animation = anim;
    e.start = start;
    e.duration = duration;
    e.dst_pixv_idx = dst;
    return e;
}

// Build a heap-allocated event array with `count` events; caller owns until
// passed to Layer::initialize().
AnimationEvent* make_events(int count, int* live_counter,
                            const float* starts, const float* durations) {
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
    CHECK(layer.active_at(0.0f) == nullptr);
}

TEST_CASE("Layer: active_at on empty layer returns nullptr", "[layer]") {
    Layer layer;
    CHECK(layer.active_at(-1.0f) == nullptr);
    CHECK(layer.active_at(0.0f) == nullptr);
    CHECK(layer.active_at(100.0f) == nullptr);
}

// ---------------------------------------------------------------------------
// initialize / reset / ownership
// ---------------------------------------------------------------------------

TEST_CASE("Layer: initialize adopts the event array", "[layer]") {
    int live = 0;
    const float starts[]    = { 0.0f, 1.0f };
    const float durations[] = { 0.5f, 0.5f };
    AnimationEvent* events = make_events(2, &live, starts, durations);
    REQUIRE(live == 2);

    Layer layer;
    layer.initialize(events, 2);

    CHECK(layer.count() == 2);
    CHECK(layer.at(0).start == 0.0f);
    CHECK(layer.at(1).start == 1.0f);
    CHECK(live == 2);
}

TEST_CASE("Layer: reset frees the array and every animation", "[layer]") {
    int live = 0;
    const float starts[]    = { 0.0f, 1.0f, 2.0f };
    const float durations[] = { 0.5f, 0.5f, 0.5f };
    Layer layer;
    layer.initialize(make_events(3, &live, starts, durations), 3);
    REQUIRE(live == 3);

    layer.reset();

    CHECK(layer.count() == 0);
    CHECK(live == 0);
    CHECK(layer.active_at(0.25f) == nullptr);
}

TEST_CASE("Layer: destructor frees the array and every animation", "[layer]") {
    int live = 0;
    const float starts[]    = { 0.0f, 1.0f };
    const float durations[] = { 0.5f, 0.5f };
    {
        Layer layer;
        layer.initialize(make_events(2, &live, starts, durations), 2);
        REQUIRE(live == 2);
    }
    CHECK(live == 0);
}

TEST_CASE("Layer: re-initialize replaces the previous events", "[layer]") {
    int live = 0;
    const float starts1[]    = { 0.0f };
    const float durations1[] = { 1.0f };
    const float starts2[]    = { 0.0f, 2.0f };
    const float durations2[] = { 1.0f, 1.0f };

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
    const float starts[]    = { 1.0f, 5.0f, 10.0f };
    const float durations[] = { 2.0f, 1.0f, 3.0f };  // [1,3) [5,6) [10,13)
    Layer layer;
    layer.initialize(make_events(3, &live, starts, durations), 3);

    const AnimationEvent* e0 = &layer.at(0);
    const AnimationEvent* e1 = &layer.at(1);
    const AnimationEvent* e2 = &layer.at(2);

    SECTION("before first event") {
        CHECK(layer.active_at(0.0f) == nullptr);
        CHECK(layer.active_at(0.999f) == nullptr);
    }
    SECTION("on event start (inclusive)") {
        CHECK(layer.active_at(1.0f) == e0);
        CHECK(layer.active_at(5.0f) == e1);
        CHECK(layer.active_at(10.0f) == e2);
    }
    SECTION("inside event") {
        CHECK(layer.active_at(2.0f) == e0);
        CHECK(layer.active_at(5.5f) == e1);
        CHECK(layer.active_at(11.5f) == e2);
    }
    SECTION("on event end (exclusive)") {
        CHECK(layer.active_at(3.0f) == nullptr);
        CHECK(layer.active_at(6.0f) == nullptr);
        CHECK(layer.active_at(13.0f) == nullptr);
    }
    SECTION("inside gap between events") {
        CHECK(layer.active_at(3.5f) == nullptr);
        CHECK(layer.active_at(8.0f) == nullptr);
    }
    SECTION("past last event") {
        CHECK(layer.active_at(20.0f) == nullptr);
    }
}

TEST_CASE("Layer: active_at on a back-to-back schedule (no gaps)", "[layer]") {
    int live = 0;
    const float starts[]    = { 0.0f, 1.0f, 2.0f };
    const float durations[] = { 1.0f, 1.0f, 1.0f };  // [0,1) [1,2) [2,3)
    Layer layer;
    layer.initialize(make_events(3, &live, starts, durations), 3);

    CHECK(layer.active_at(0.0f) == &layer.at(0));
    CHECK(layer.active_at(0.999f) == &layer.at(0));
    CHECK(layer.active_at(1.0f) == &layer.at(1));
    CHECK(layer.active_at(2.0f) == &layer.at(2));
    CHECK(layer.active_at(3.0f) == nullptr);
}
