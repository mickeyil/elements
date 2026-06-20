#pragma once

#include <cstddef>
#include <cstdint>

#include "colors.h"
#include "device_identity.h"
#include "frame_output.h"
#include "hardware_profile.h"
#include "udp_transport.h"

class Strip;

// FrameOutput for the sim build: sends each frame as an RGB preview
// packet to the controller's frame port, where the UI renders it.
// The uid slot lets the controller demux multiple sims, since on
// loopback every sim shares the same source address.
//
// Packet (multi-byte fields little-endian):
//   uid=16B | frame_index=u32 | t_program=f32 | rgb bytes
//
// This is its own protocol, not the controller link; the destination
// comes from sim CLI flags. The firmware counterpart is EspFrameOutput.

constexpr size_t FRAME_PREVIEW_HEADER_BYTES = UID_SIZE + 4 + 4;

static_assert(FRAME_PREVIEW_HEADER_BYTES + MAX_STRIP_PIXELS * sizeof(rgb_t)
                  <= MAX_PAYLOAD_SIZE,
              "a full-length preview frame must fit one UDP packet");

class SimFrameOutput : public FrameOutput
{
public:
    SimFrameOutput(UdpTransport& udp, const DeviceIdentity& identity,
                   uint32_t dst_ip, uint16_t dst_port);

    // No output transform: the preview carries raw program-space RGB.
    // Gamma compensates physical LEDs; the UI screen does its own.
    void apply_profile(const HardwareProfile&) override {}

    // Send the frame, best effort: a failed send closes the socket
    // (it reopens on the next write) and the frame is dropped.
    void write(const Strip& strip, float t_program) override;

private:
    UdpTransport&  _udp;
    const uint32_t _dst_ip;
    const uint16_t _dst_port;

    uint8_t  _uid_slot[UID_SIZE] = {};  // null-padded wire form
    uint32_t _frame_index = 0;          // gaps tell the receiver about drops
};
