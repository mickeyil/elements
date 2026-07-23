#pragma once

class Strip;
struct HardwareProfile;

// The platform's frame destination: where rendered frames leave
// shared code. The owner applies the hardware profile once at setup,
// then calls write() once per rendered frame; the implementation
// turns the program-space RGB strip into platform output.
//
// t_program fills the sim preview header; the firmware ignores it.
//
// Implementations:
//   - EspFrameOutput (firmware: gamma, channel order, FastLED)
//   - SimFrameOutput (sim: frame-preview UDP to the controller)

class FrameOutput
{
public:
    virtual ~FrameOutput() = default;

    // One-time setup, before the first write(). EspFrameOutput builds
    // its gamma LUT and latches the channel order here.
    virtual void apply_profile(const HardwareProfile& profile) = 0;

    // Write one frame. The strip holds pre-gamma program-space RGB.
    virtual void write(const Strip& strip, float t_program) = 0;
};
