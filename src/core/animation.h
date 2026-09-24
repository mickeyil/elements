#pragma once

#include "core/program_time.h"

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
//                 state and `t` alone. It is also called once with `t` equal
//                 to the event duration: the endpoint sample a later event
//                 picks up. An event no frame ever showed still gets
//                 initialize() and that one render().
//
// Time: `t` is whole milliseconds since the event started, and can be as
// large as MAX_PROGRAM_MS (30 days). float32 cannot hold that with ms
// precision, so reduce it (modulo, comparison, or double arithmetic) to
// something small BEFORE converting to float32, once per frame; per-pixel
// math stays float32. Double is software on ESP32, so keep double math per
// frame, never per pixel.

class Animation
{
public:
    virtual ~Animation() = default;

    // `src` and `work` are nullptr unless the event declared the
    // corresponding PixelView indices.
    virtual void initialize(const PixelView* src, PixelView* work) {}

    // Render one frame at `t` (elapsed since event start) into `dst`.
    virtual void render(PixelView& dst, ProgramDuration t) = 0;
};
