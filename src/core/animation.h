#pragma once

class PixelView;

// Animation is the visual primitive interface. The decoder builds one instance
// per AnimationEvent; the engine drives it as the timeline plays.
//
// The engine may skip frames or start late, so an animation must not care
// how many times it was rendered before, or when:
//   initialize()  runs once, when the event's start time is reached. Capture
//                 anything needed from `src` here (snapshot it into `work`);
//                 `src` may be overwritten afterwards.
//   render()      must fully define every pixel of `dst` from the captured
//                 state and `t_animation` alone. It is also called once with
//                 `t_animation` equal to the event duration: the endpoint
//                 sample a later event picks up. An event no frame ever
//                 showed still gets initialize() and that one render().

class Animation
{
public:
    virtual ~Animation() = default;

    // `src` and `work` are nullptr unless the event declared the
    // corresponding PixelView indices.
    virtual void initialize(const PixelView* src, PixelView* work) {}

    // Render one frame at `t_animation` (seconds since event start) into `dst`.
    virtual void render(PixelView& dst, float t_animation) = 0;
};
