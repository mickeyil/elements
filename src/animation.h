#pragma once

class PixelView;

// Animation is the visual primitive interface. The decoder builds one instance
// per AnimationEvent; the engine activates and renders them as the timeline
// plays.
//
// initialize() runs once on first activation -- a chance to read `src` or
// snapshot it into `work`. render() runs every frame thereafter and must fully
// define every pixel in `dst` (the engine assumes no carryover from the
// previous frame).

class Animation {
public:
    virtual ~Animation() = default;

    // First-activation hook. `src` and `work` are nullptr unless the event
    // declared the corresponding PixelView indices.
    virtual void initialize(const PixelView* src, PixelView* work) {}

    // Render one frame at `t_animation` (seconds since event start) into `dst`.
    virtual void render(PixelView& dst, float t_animation) = 0;
};
