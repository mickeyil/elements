#include <catch2/catch_test_macros.hpp>

#include <cstdint>
#include <string>
#include <vector>

#include "core/animation.h"
#include "core/colors.h"
#include "core/copy_ops.h"
#include "core/engine.h"
#include "core/layer.h"
#include "core/pixel_buffer_pool.h"
#include "core/pixel_views.h"
#include "core/program.h"
#include "core/runtime_constants.h"
#include "core/strip.h"

namespace {

// Animation that records every initialize/render call so tests can verify
// activation timing, frame counts, and t_animation values. Optionally writes
// `_render_value` to every pixel of dst on render() so the strip output can
// be observed end-to-end.
class FakeAnim : public Animation {
public:
    void initialize(const PixelView* src, PixelView* work) override {
        _init_count++;
        _last_src = src;
        _last_work = work;
    }
    void render(PixelView& dst, float t_animation) override {
        _render_count++;
        _last_t = t_animation;
        _last_dst = &dst;
        for (uint16_t i = 0; i < dst.size(); i++) {
            dst[i] = _render_value;
        }
    }

    int init_count() const { return _init_count; }
    int render_count() const { return _render_count; }
    float last_t() const { return _last_t; }
    PixelView* last_dst() const { return _last_dst; }
    const PixelView* last_src() const { return _last_src; }
    PixelView* last_work() const { return _last_work; }

    void set_render_value(hsva_t v) { _render_value = v; }

private:
    int _init_count = 0;
    int _render_count = 0;
    float _last_t = -1.0f;
    PixelView* _last_dst = nullptr;
    const PixelView* _last_src = nullptr;
    PixelView* _last_work = nullptr;
    hsva_t _render_value = hsva_t(0.0f, 0.0f, 0.0f, 1.0f);
};

// Animation that copies its src view's first pixel into its dst on render.
// Used to make copy-op-before-render order observable through the strip.
class SrcReadAnim : public Animation {
public:
    void initialize(const PixelView* src, PixelView*) override { _src = src; }
    void render(PixelView& dst, float) override {
        if (_src && _src->size() > 0 && dst.size() > 0) {
            dst[0] = (*_src)[0];
        }
    }
private:
    const PixelView* _src = nullptr;
};

// Build a 1-buffer, 1-view, no-layers Program. View 0 binds buffer 0 with
// identity storage and identity physical mapping.
Program* basic_program(uint32_t duration_ms, uint16_t buffer_size = 1) {
    Program* p = new Program();
    p->duration = ProgramDuration{duration_ms};
    REQUIRE(p->pixel_buffer_pool.initialize(&buffer_size, 1));
    PixelViewSpec spec;
    spec.buffer_idx = 0;
    spec.size = buffer_size;
    spec.storage_identity = true;
    spec.has_physical_mapping = true;
    spec.physical_identity = true;
    REQUIRE(p->pixel_views.initialize(p->pixel_buffer_pool, &spec, 1));
    return p;
}

void give_layers(Program* p, uint8_t count) {
    p->layer_count = count;
    p->layers = new Layer[count];
}

AnimationEvent make_event(Animation* anim, uint32_t start_ms, uint32_t duration_ms,
                          uint16_t dst, uint16_t src = PIXV_NONE,
                          uint16_t work = PIXV_NONE) {
    AnimationEvent e;
    e.animation = anim;
    e.start = ProgramTime{start_ms};
    e.duration = ProgramDuration{duration_ms};
    e.dst_pixv_idx = dst;
    e.src_pixv_idx = src;
    e.work_pixv_idx = work;
    return e;
}

void install_event(Layer& layer, AnimationEvent e) {
    AnimationEvent* arr = new AnimationEvent[1];
    arr[0] = e;
    layer.initialize(arr, 1);
}

}  // namespace

// ---------------------------------------------------------------------------
// Construction / boundaries
// ---------------------------------------------------------------------------

TEST_CASE("Engine: create(nullptr) yields a valid Engine that renders nothing",
          "[engine]") {
    Engine* eng = Engine::create(nullptr);
    REQUIRE(eng != nullptr);

    Strip strip;
    REQUIRE(strip.resize(2));
    CHECK_FALSE(eng->render_frame(ProgramTime{0}, strip));

    delete eng;
}

TEST_CASE("Engine: render_frame on empty Program clears the strip", "[engine]") {
    Program* prog = basic_program(/*duration_ms*/ 1000);
    Engine* eng = Engine::create(prog);
    REQUIRE(eng != nullptr);

    Strip strip;
    REQUIRE(strip.resize(1));
    strip[0] = rgb_t(99, 99, 99);

    REQUIRE(eng->render_frame(ProgramTime{500}, strip));
    CHECK(strip[0].r == 0);

    delete eng;
}

TEST_CASE("Engine: render_frame returns false outside [0, duration)", "[engine]") {
    Program* prog = basic_program(1000);
    Engine* eng = Engine::create(prog);
    Strip strip; REQUIRE(strip.resize(1));

    CHECK(eng->render_frame(ProgramTime{0}, strip));        // inclusive lower
    CHECK(eng->render_frame(ProgramTime{999}, strip));
    CHECK_FALSE(eng->render_frame(ProgramTime{1000}, strip));  // exclusive upper
    CHECK_FALSE(eng->render_frame(ProgramTime{2000}, strip));

    delete eng;
}

// ---------------------------------------------------------------------------
// Event activation lifecycle
// ---------------------------------------------------------------------------

TEST_CASE("Engine: initialize fires once on first activation, render every frame",
          "[engine]") {
    Program* prog = basic_program(2000);
    give_layers(prog, 1);
    auto* anim = new FakeAnim();
    anim->set_render_value(hsva_t(0.0f, 1.0f, 1.0f, 1.0f));  // opaque red
    install_event(prog->layers[0], make_event(anim, /*start_ms*/ 500, /*dur_ms*/ 1000, /*dst*/ 0));

    Engine* eng = Engine::create(prog);
    Strip strip; REQUIRE(strip.resize(1));

    REQUIRE(eng->render_frame(ProgramTime{0}, strip));   // before event
    CHECK(anim->init_count() == 0);
    CHECK(anim->render_count() == 0);
    CHECK(strip[0].r == 0);

    REQUIRE(eng->render_frame(ProgramTime{500}, strip));   // first activation
    CHECK(anim->init_count() == 1);
    CHECK(anim->render_count() == 1);
    CHECK(anim->last_t() == 0.0f);

    REQUIRE(eng->render_frame(ProgramTime{1000}, strip));   // mid event
    CHECK(anim->init_count() == 1);            // not re-initialized
    CHECK(anim->render_count() == 2);
    CHECK(anim->last_t() == 0.5f);

    REQUIRE(eng->render_frame(ProgramTime{1600}, strip));   // past event end
    CHECK(anim->init_count() == 1);
    CHECK(anim->render_count() == 3);          // retired with its endpoint render
    CHECK(anim->last_t() == 1.0f);
    CHECK(strip[0].r == 0);                    // no active layer

    delete eng;
}

TEST_CASE("Engine: cursor advances to the next event on the same layer", "[engine]") {
    Program* prog = basic_program(2000);
    give_layers(prog, 1);
    auto* a1 = new FakeAnim();
    auto* a2 = new FakeAnim();
    AnimationEvent* events = new AnimationEvent[2];
    events[0] = make_event(a1, 0, 500, 0);
    events[1] = make_event(a2, 500, 500, 0);
    prog->layers[0].initialize(events, 2);

    Engine* eng = Engine::create(prog);
    Strip strip; REQUIRE(strip.resize(1));

    REQUIRE(eng->render_frame(ProgramTime{250}, strip));
    CHECK(a1->init_count() == 1);
    CHECK(a1->render_count() == 1);
    CHECK(a2->init_count() == 0);

    REQUIRE(eng->render_frame(ProgramTime{750}, strip));
    CHECK(a1->render_count() == 2);   // retired with its endpoint render
    CHECK(a1->last_t() == 0.5f);
    CHECK(a2->init_count() == 1);     // second event activated
    CHECK(a2->render_count() == 1);
    CHECK(a2->last_t() == 0.25f);

    delete eng;
}

TEST_CASE("Engine: passes src and work views to initialize when present", "[engine]") {
    // Two extra views (1 src, 1 work) on top of the basic dst view.
    Program* prog = new Program();
    prog->duration = ProgramDuration{1000};
    const uint16_t sizes[] = { 1, 1, 1 };
    REQUIRE(prog->pixel_buffer_pool.initialize(sizes, 3));
    PixelViewSpec specs[3];
    for (int i = 0; i < 3; i++) {
        specs[i].buffer_idx = static_cast<uint16_t>(i);
        specs[i].size = 1;
        specs[i].storage_identity = true;
        specs[i].has_physical_mapping = (i == 0);
        specs[i].physical_identity = (i == 0);
    }
    REQUIRE(prog->pixel_views.initialize(prog->pixel_buffer_pool, specs, 3));

    give_layers(prog, 1);
    auto* anim = new FakeAnim();
    install_event(prog->layers[0],
                  make_event(anim, 0, 1000, /*dst*/ 0, /*src*/ 1, /*work*/ 2));

    Engine* eng = Engine::create(prog);
    Strip strip; REQUIRE(strip.resize(1));

    REQUIRE(eng->render_frame(ProgramTime{100}, strip));
    REQUIRE(anim->init_count() == 1);
    CHECK(anim->last_src() == &prog->pixel_views.at(1));
    CHECK(anim->last_work() == &prog->pixel_views.at(2));
    CHECK(anim->last_dst() == &prog->pixel_views.at(0));

    delete eng;
}

// ---------------------------------------------------------------------------
// Multi-layer
// ---------------------------------------------------------------------------

TEST_CASE("Engine: layers track their own active events independently", "[engine]") {
    // Two views on two buffers, each mapped to a different physical LED.
    Program* prog = new Program();
    prog->duration = ProgramDuration{2000};
    const uint16_t sizes[] = { 1, 1 };
    REQUIRE(prog->pixel_buffer_pool.initialize(sizes, 2));
    const uint16_t phys0[] = { 0 };
    const uint16_t phys1[] = { 1 };
    PixelViewSpec specs[2];
    specs[0].buffer_idx = 0; specs[0].size = 1;
    specs[0].storage_identity = true;
    specs[0].has_physical_mapping = true;
    specs[0].physical_indices = phys0;
    specs[1].buffer_idx = 1; specs[1].size = 1;
    specs[1].storage_identity = true;
    specs[1].has_physical_mapping = true;
    specs[1].physical_indices = phys1;
    REQUIRE(prog->pixel_views.initialize(prog->pixel_buffer_pool, specs, 2));

    give_layers(prog, 2);
    auto* a0 = new FakeAnim();
    auto* a1 = new FakeAnim();
    a0->set_render_value(hsva_t(0.0f,   1.0f, 1.0f, 1.0f));
    a1->set_render_value(hsva_t(120.0f, 1.0f, 1.0f, 1.0f));
    install_event(prog->layers[0], make_event(a0, 0, 1000, /*dst*/ 0));
    install_event(prog->layers[1], make_event(a1, 1000, 1000, /*dst*/ 1));

    Engine* eng = Engine::create(prog);
    Strip strip; REQUIRE(strip.resize(2));

    // t=0.5: only layer 0 active
    REQUIRE(eng->render_frame(ProgramTime{500}, strip));
    CHECK(a0->render_count() == 1);
    CHECK(a1->render_count() == 0);
    rgb_t red = hsv_to_rgb(0.0f, 1.0f, 1.0f);
    CHECK(strip[0].r == red.r);
    CHECK(strip[0].g == red.g);
    CHECK(strip[0].b == red.b);
    CHECK(strip[1].r == 0);

    // t=1.5: only layer 1 active; layer 0's event was retired with its
    // endpoint render on the way.
    REQUIRE(eng->render_frame(ProgramTime{1500}, strip));
    CHECK(a0->render_count() == 2);
    CHECK(a1->render_count() == 1);
    rgb_t green = hsv_to_rgb(120.0f, 1.0f, 1.0f);
    CHECK(strip[0].r == 0);
    CHECK(strip[1].r == green.r);
    CHECK(strip[1].g == green.g);
    CHECK(strip[1].b == green.b);

    delete eng;
}

// ---------------------------------------------------------------------------
// Copy ops
// ---------------------------------------------------------------------------

TEST_CASE("Engine: copy ops fire before layers render", "[engine]") {
    // Three buffers: 0 pre-filled with green; 1 receives the copy; 2 is the
    // animation's dst (with physical mapping). SrcReadAnim reads view 1 and
    // writes it to view 2. If the copy ran before render, the strip is green;
    // if it ran after (or not at all), the strip is black.
    Program* prog = new Program();
    prog->duration = ProgramDuration{1000};
    const uint16_t sizes[] = { 1, 1, 1 };
    REQUIRE(prog->pixel_buffer_pool.initialize(sizes, 3));

    PixelViewSpec specs[3];
    for (int i = 0; i < 3; i++) {
        specs[i].buffer_idx = static_cast<uint16_t>(i);
        specs[i].size = 1;
        specs[i].storage_identity = true;
        specs[i].has_physical_mapping = (i == 2);
        specs[i].physical_identity = (i == 2);
    }
    REQUIRE(prog->pixel_views.initialize(prog->pixel_buffer_pool, specs, 3));

    prog->pixel_views.at(0)[0] = hsva_t(120.0f, 1.0f, 1.0f, 1.0f);  // green seed

    CopyOp op;
    op.at = ProgramTime{0};
    op.src_pixv_idx = 0;
    op.dst_pixv_idx = 1;
    REQUIRE(prog->copy_ops.initialize(&op, 1));

    give_layers(prog, 1);
    install_event(prog->layers[0],
                  make_event(new SrcReadAnim(), 0, 1000, /*dst*/ 2, /*src*/ 1));

    Engine* eng = Engine::create(prog);
    Strip strip; REQUIRE(strip.resize(1));

    REQUIRE(eng->render_frame(ProgramTime{500}, strip));
    rgb_t green = hsv_to_rgb(120.0f, 1.0f, 1.0f);
    CHECK(strip[0].r == green.r);
    CHECK(strip[0].g == green.g);
    CHECK(strip[0].b == green.b);

    delete eng;
}

TEST_CASE("Engine: copy_op cursor only advances past due ops", "[engine]") {
    Program* prog = new Program();
    prog->duration = ProgramDuration{2000};
    const uint16_t sizes[] = { 1, 1, 1 };
    REQUIRE(prog->pixel_buffer_pool.initialize(sizes, 3));
    PixelViewSpec specs[3];
    for (int i = 0; i < 3; i++) {
        specs[i].buffer_idx = static_cast<uint16_t>(i);
        specs[i].size = 1;
        specs[i].storage_identity = true;
        specs[i].has_physical_mapping = false;
    }
    REQUIRE(prog->pixel_views.initialize(prog->pixel_buffer_pool, specs, 3));

    prog->pixel_views.at(0)[0] = hsva_t(60.0f,  1.0f, 1.0f, 1.0f);
    prog->pixel_views.at(1)[0] = hsva_t(180.0f, 1.0f, 1.0f, 1.0f);

    CopyOp ops[2];
    ops[0].at = ProgramTime{500}; ops[0].src_pixv_idx = 0; ops[0].dst_pixv_idx = 2;
    ops[1].at = ProgramTime{1500}; ops[1].src_pixv_idx = 1; ops[1].dst_pixv_idx = 2;
    REQUIRE(prog->copy_ops.initialize(ops, 2));

    Engine* eng = Engine::create(prog);
    Strip strip; REQUIRE(strip.resize(1));

    REQUIRE(eng->render_frame(ProgramTime{250}, strip));        // before any op
    CHECK(prog->pixel_views.at(2)[0].h == 0.0f);

    REQUIRE(eng->render_frame(ProgramTime{1000}, strip));         // op 0 due
    CHECK(prog->pixel_views.at(2)[0].h == 60.0f);

    REQUIRE(eng->render_frame(ProgramTime{1750}, strip));        // op 1 due
    CHECK(prog->pixel_views.at(2)[0].h == 180.0f);

    delete eng;
}

// ---------------------------------------------------------------------------
// reset()
// ---------------------------------------------------------------------------

TEST_CASE("Engine: reset re-arms initialize for the next render", "[engine]") {
    Program* prog = basic_program(2000);
    give_layers(prog, 1);
    auto* anim = new FakeAnim();
    install_event(prog->layers[0], make_event(anim, 0, 1000, /*dst*/ 0));

    Engine* eng = Engine::create(prog);
    Strip strip; REQUIRE(strip.resize(1));

    REQUIRE(eng->render_frame(ProgramTime{100}, strip));
    CHECK(anim->init_count() == 1);
    CHECK(anim->render_count() == 1);

    eng->reset();

    REQUIRE(eng->render_frame(ProgramTime{200}, strip));
    CHECK(anim->init_count() == 2);   // re-armed
    CHECK(anim->render_count() == 2);

    delete eng;
}

TEST_CASE("Engine: reset rewinds the copy-op cursor", "[engine]") {
    Program* prog = new Program();
    prog->duration = ProgramDuration{1000};
    const uint16_t sizes[] = { 1, 1, 1 };
    REQUIRE(prog->pixel_buffer_pool.initialize(sizes, 3));
    PixelViewSpec specs[3];
    for (int i = 0; i < 3; i++) {
        specs[i].buffer_idx = static_cast<uint16_t>(i);
        specs[i].size = 1;
        specs[i].storage_identity = true;
        specs[i].has_physical_mapping = (i == 2);
        specs[i].physical_identity = (i == 2);
    }
    REQUIRE(prog->pixel_views.initialize(prog->pixel_buffer_pool, specs, 3));

    prog->pixel_views.at(0)[0] = hsva_t(120.0f, 1.0f, 1.0f, 1.0f);

    CopyOp op;
    op.at = ProgramTime{0};
    op.src_pixv_idx = 0;
    op.dst_pixv_idx = 1;
    REQUIRE(prog->copy_ops.initialize(&op, 1));

    give_layers(prog, 1);
    install_event(prog->layers[0],
                  make_event(new SrcReadAnim(), 0, 1000, /*dst*/ 2, /*src*/ 1));

    Engine* eng = Engine::create(prog);
    Strip strip; REQUIRE(strip.resize(1));

    REQUIRE(eng->render_frame(ProgramTime{500}, strip));
    rgb_t green = hsv_to_rgb(120.0f, 1.0f, 1.0f);
    REQUIRE(strip[0].r == green.r);

    eng->reset();
    // Reset zeroes every pool buffer (including the green seed in buffer 0).
    // Re-seed so the replayed copy has data.
    prog->pixel_views.at(0)[0] = hsva_t(120.0f, 1.0f, 1.0f, 1.0f);

    REQUIRE(eng->render_frame(ProgramTime{500}, strip));
    CHECK(strip[0].r == green.r);   // copy ran again post-reset

    delete eng;
}

TEST_CASE("Engine: reset zeroes every pool buffer", "[engine]") {
    Program* prog = basic_program(1000);
    give_layers(prog, 1);
    auto* anim = new FakeAnim();
    anim->set_render_value(hsva_t(45.0f, 0.5f, 0.5f, 1.0f));
    install_event(prog->layers[0], make_event(anim, 0, 1000, /*dst*/ 0));

    Engine* eng = Engine::create(prog);
    Strip strip; REQUIRE(strip.resize(1));

    REQUIRE(eng->render_frame(ProgramTime{500}, strip));
    REQUIRE(prog->pixel_views.at(0)[0].h == 45.0f);

    eng->reset();

    CHECK(prog->pixel_views.at(0)[0].h == 0.0f);
    CHECK(prog->pixel_views.at(0)[0].s == 0.0f);
    CHECK(prog->pixel_views.at(0)[0].v == 0.0f);
    CHECK(prog->pixel_views.at(0)[0].a == 0.0f);

    delete eng;
}

// ---------------------------------------------------------------------------
// Same-`at` ordering and same-LED layer overlap
// ---------------------------------------------------------------------------

TEST_CASE("Engine: chained same-`at` copy ops execute in table order",
          "[engine]") {
    // Two copy ops at the same time, chained: view0 -> view1, then view1 ->
    // view2. The animation reads view2 and writes its dst. If both ops run in
    // table order, view2 carries the seed; reverse order would leave view2
    // untouched (still zero) and the strip black.
    Program* prog = new Program();
    prog->duration = ProgramDuration{1000};
    const uint16_t sizes[] = { 1, 1, 1, 1 };
    REQUIRE(prog->pixel_buffer_pool.initialize(sizes, 4));
    PixelViewSpec specs[4];
    for (int i = 0; i < 4; i++) {
        specs[i].buffer_idx = static_cast<uint16_t>(i);
        specs[i].size = 1;
        specs[i].storage_identity = true;
        specs[i].has_physical_mapping = (i == 3);
        specs[i].physical_identity = (i == 3);
    }
    REQUIRE(prog->pixel_views.initialize(prog->pixel_buffer_pool, specs, 4));

    prog->pixel_views.at(0)[0] = hsva_t(120.0f, 1.0f, 1.0f, 1.0f);  // green seed

    CopyOp ops[2];
    ops[0].at = ProgramTime{0}; ops[0].src_pixv_idx = 0; ops[0].dst_pixv_idx = 1;
    ops[1].at = ProgramTime{0}; ops[1].src_pixv_idx = 1; ops[1].dst_pixv_idx = 2;
    REQUIRE(prog->copy_ops.initialize(ops, 2));

    give_layers(prog, 1);
    install_event(prog->layers[0],
                  make_event(new SrcReadAnim(), 0, 1000, /*dst*/ 3, /*src*/ 2));

    Engine* eng = Engine::create(prog);
    Strip strip; REQUIRE(strip.resize(1));

    REQUIRE(eng->render_frame(ProgramTime{500}, strip));

    rgb_t green = hsv_to_rgb(120.0f, 1.0f, 1.0f);
    CHECK(strip[0].r == green.r);
    CHECK(strip[0].g == green.g);
    CHECK(strip[0].b == green.b);

    delete eng;
}

TEST_CASE("Engine: top layer wins on a shared physical LED", "[engine]") {
    // Two layers, two views on different buffers but mapped to the same
    // physical LED. Both events active simultaneously, both opaque. The
    // compositor walks active_dst_views[] bottom-to-top, so the top layer's
    // green must overwrite the bottom layer's red.
    Program* prog = new Program();
    prog->duration = ProgramDuration{1000};
    const uint16_t sizes[] = { 1, 1 };
    REQUIRE(prog->pixel_buffer_pool.initialize(sizes, 2));
    const uint16_t shared_phys[] = { 0 };
    PixelViewSpec specs[2];
    for (int i = 0; i < 2; i++) {
        specs[i].buffer_idx = static_cast<uint16_t>(i);
        specs[i].size = 1;
        specs[i].storage_identity = true;
        specs[i].has_physical_mapping = true;
        specs[i].physical_indices = shared_phys;
    }
    REQUIRE(prog->pixel_views.initialize(prog->pixel_buffer_pool, specs, 2));

    give_layers(prog, 2);
    auto* bottom = new FakeAnim();
    auto* top    = new FakeAnim();
    bottom->set_render_value(hsva_t(0.0f,   1.0f, 1.0f, 1.0f));   // red
    top->set_render_value(   hsva_t(120.0f, 1.0f, 1.0f, 1.0f));   // green
    install_event(prog->layers[0], make_event(bottom, 0, 1000, /*dst*/ 0));
    install_event(prog->layers[1], make_event(top,    0, 1000, /*dst*/ 1));

    Engine* eng = Engine::create(prog);
    Strip strip; REQUIRE(strip.resize(1));

    REQUIRE(eng->render_frame(ProgramTime{500}, strip));

    rgb_t green = hsv_to_rgb(120.0f, 1.0f, 1.0f);
    CHECK(strip[0].r == green.r);
    CHECK(strip[0].g == green.g);
    CHECK(strip[0].b == green.b);

    delete eng;
}

// ---------------------------------------------------------------------------
// Timeline order: endings, then copies, then starts, then drawing
// ---------------------------------------------------------------------------

namespace {

using Log = std::vector<std::string>;

// Animation that appends every call to a shared log as "name.init" or
// "name.render(t_ms)". render() writes its time into every dst pixel's hue,
// so a snapshot of its output tells which render produced it; initialize()
// remembers the hue it saw in src.
class LogAnim : public Animation
{
public:
    LogAnim(Log& log, const char* name) : _log(log), _name(name) {}

    void initialize(const PixelView* src, PixelView*) override {
        _log.push_back(_name + ".init");
        if (src != nullptr && src->size() > 0) {
            _seen_src_h = (*src)[0].h;
        }
    }
    void render(PixelView& dst, float t_animation) override {
        const int t_ms = static_cast<int>(t_animation * 1000.0f + 0.5f);
        _log.push_back(_name + ".render(" + std::to_string(t_ms) + ")");
        for (uint16_t i = 0; i < dst.size(); i++) {
            dst[i] = hsva_t(t_animation, 1.0f, 1.0f, 1.0f);
        }
    }

    float seen_src_h() const { return _seen_src_h; }

private:
    Log& _log;
    std::string _name;
    float _seen_src_h = -1.0f;   // -1 = initialize() saw no src
};

// One layer with A on [0,100) and B on [100,200), both drawing view 0.
Program* adjacent_pair(Log& log, LogAnim*& a, LogAnim*& b) {
    Program* prog = basic_program(1000);
    give_layers(prog, 1);
    a = new LogAnim(log, "A");
    b = new LogAnim(log, "B");
    AnimationEvent* events = new AnimationEvent[2];
    events[0] = make_event(a, 0, 100, 0);
    events[1] = make_event(b, 100, 100, 0);
    prog->layers[0].initialize(events, 2);
    return prog;
}

// Layer 0: A on [0,100) draws view 0. A copy op at 100 moves view 0 into
// view 1. Layer 1: B on [100,200) reads view 1 in initialize() and draws
// view 0. Only the order endpoint, copy, start hands B the hue A wrote at
// its end (0.1): running the copy first hands it A's frame-0 hue (0), and
// starting B first hands it the untouched buffer (0).
Program* copy_handoff(Log& log, LogAnim*& a, LogAnim*& b) {
    Program* prog = new Program();
    prog->duration = ProgramDuration{1000};
    const uint16_t sizes[] = { 1, 1 };
    REQUIRE(prog->pixel_buffer_pool.initialize(sizes, 2));
    PixelViewSpec specs[2];
    for (int i = 0; i < 2; i++) {
        specs[i].buffer_idx = static_cast<uint16_t>(i);
        specs[i].size = 1;
        specs[i].storage_identity = true;
        specs[i].has_physical_mapping = (i == 0);
        specs[i].physical_identity = (i == 0);
    }
    REQUIRE(prog->pixel_views.initialize(prog->pixel_buffer_pool, specs, 2));

    CopyOp op;
    op.at = ProgramTime{100};
    op.src_pixv_idx = 0;
    op.dst_pixv_idx = 1;
    REQUIRE(prog->copy_ops.initialize(&op, 1));

    give_layers(prog, 2);
    a = new LogAnim(log, "A");
    b = new LogAnim(log, "B");
    install_event(prog->layers[0], make_event(a, 0, 100, /*dst*/ 0));
    install_event(prog->layers[1], make_event(b, 100, 100, /*dst*/ 0, /*src*/ 1));
    return prog;
}

}  // namespace

TEST_CASE("Engine: at one boundary every ending renders its endpoint before any start initializes",
          "[engine]") {
    Program* prog = basic_program(1000);
    give_layers(prog, 2);
    Log log;
    auto* a = new LogAnim(log, "A");
    auto* b = new LogAnim(log, "B");
    auto* c = new LogAnim(log, "C");
    auto* d = new LogAnim(log, "D");
    AnimationEvent* l0 = new AnimationEvent[2];
    l0[0] = make_event(a, 0, 100, 0);
    l0[1] = make_event(c, 100, 100, 0);
    prog->layers[0].initialize(l0, 2);
    AnimationEvent* l1 = new AnimationEvent[2];
    l1[0] = make_event(b, 0, 100, 0);
    l1[1] = make_event(d, 100, 100, 0);
    prog->layers[1].initialize(l1, 2);

    Engine* eng = Engine::create(prog);
    Strip strip; REQUIRE(strip.resize(1));

    REQUIRE(eng->render_frame(ProgramTime{0}, strip));
    CHECK(log == Log{"A.init", "B.init", "A.render(0)", "B.render(0)"});

    log.clear();
    REQUIRE(eng->render_frame(ProgramTime{100}, strip));
    CHECK(log == Log{"A.render(100)", "B.render(100)",
                     "C.init", "D.init",
                     "C.render(0)", "D.render(0)"});

    delete eng;
}

TEST_CASE("Engine: a copy at a boundary carries the endpoint to the event starting there",
          "[engine]") {
    Log log;
    LogAnim* a = nullptr;
    LogAnim* b = nullptr;
    Program* prog = copy_handoff(log, a, b);
    Engine* eng = Engine::create(prog);
    Strip strip; REQUIRE(strip.resize(1));

    SECTION("frames on both sides of the boundary") {
        REQUIRE(eng->render_frame(ProgramTime{0}, strip));
        log.clear();
        REQUIRE(eng->render_frame(ProgramTime{150}, strip));
        CHECK(log == Log{"A.render(100)", "B.init", "B.render(50)"});
        CHECK(b->seen_src_h() == 0.1f);
    }

    SECTION("late first frame") {
        REQUIRE(eng->render_frame(ProgramTime{150}, strip));
        CHECK(log == Log{"A.init", "A.render(100)", "B.init", "B.render(50)"});
        CHECK(b->seen_src_h() == 0.1f);
    }

    SECTION("reset, then a late first frame") {
        REQUIRE(eng->render_frame(ProgramTime{0}, strip));
        REQUIRE(eng->render_frame(ProgramTime{150}, strip));
        eng->reset();
        log.clear();
        REQUIRE(eng->render_frame(ProgramTime{150}, strip));
        CHECK(log == Log{"A.init", "A.render(100)", "B.init", "B.render(50)"});
        CHECK(b->seen_src_h() == 0.1f);
    }

    delete eng;
}

TEST_CASE("Engine: an event no frame shows still gets initialize and its endpoint render",
          "[engine]") {
    Program* prog = basic_program(1000);
    give_layers(prog, 1);
    Log log;
    auto* a = new LogAnim(log, "A");
    install_event(prog->layers[0], make_event(a, 100, 100, 0));

    Engine* eng = Engine::create(prog);
    Strip strip; REQUIRE(strip.resize(1));

    REQUIRE(eng->render_frame(ProgramTime{0}, strip));
    REQUIRE(eng->render_frame(ProgramTime{200}, strip));   // exactly at A's end
    CHECK(log == Log{"A.init", "A.render(100)"});

    // The endpoint render lands in the buffer but is never shown.
    CHECK(prog->pixel_views.at(0)[0].a == 1.0f);
    CHECK(strip[0].r == 0);
    CHECK(strip[0].g == 0);
    CHECK(strip[0].b == 0);

    delete eng;
}

TEST_CASE("Engine: frames at T-1, T, T again and T+1 around a shared boundary",
          "[engine]") {
    Log log;
    LogAnim* a = nullptr;
    LogAnim* b = nullptr;
    Program* prog = adjacent_pair(log, a, b);
    Engine* eng = Engine::create(prog);
    Strip strip; REQUIRE(strip.resize(1));

    REQUIRE(eng->render_frame(ProgramTime{99}, strip));
    CHECK(log == Log{"A.init", "A.render(99)"});

    log.clear();
    REQUIRE(eng->render_frame(ProgramTime{100}, strip));
    CHECK(log == Log{"A.render(100)", "B.init", "B.render(0)"});

    // A repeated time applies no transition twice.
    log.clear();
    REQUIRE(eng->render_frame(ProgramTime{100}, strip));
    CHECK(log == Log{"B.render(0)"});

    log.clear();
    REQUIRE(eng->render_frame(ProgramTime{101}, strip));
    CHECK(log == Log{"B.render(1)"});

    delete eng;
}
