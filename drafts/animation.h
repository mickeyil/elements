#pragma once

// Draft-only API.
//
// Proposed animation interface for the PixelView-based runtime. Decoder-created
// animation instances are configured once, then initialized on first activation
// and rendered into destination PixelViews.
//
// Draft note:
// Animation instances are assumed to hold immutable decoded configuration.
// If an animation needs mutable per-activation state, that state should live
// in `work` storage or be fully re-established by initialize() before render()
// is called. The engine-side reset model depends on that constraint.

class PixelView;

class Animation {
public:
    virtual ~Animation() = default;

    // Called on first activation of the event.
    //
    // - src: optional initialization input
    // - work: optional persistent event-local pixel storage
    virtual void initialize(const PixelView* src, PixelView* work) {}

    // Render the current frame into dst. dst is always present.
    //
    // Contract: every logical pixel in dst should be fully defined on every
    // render call. The engine should not rely on stale dst contents for visual
    // correctness.
    virtual void render(PixelView& dst, float t_rel) = 0;
};
