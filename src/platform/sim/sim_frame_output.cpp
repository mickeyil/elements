#include "platform/sim/sim_frame_output.h"

#include <algorithm>
#include <cstring>

#include "core/strip.h"
#include "controller/wire_writer.h"

SimFrameOutput::SimFrameOutput(UdpTransport& udp,
                               const DeviceIdentity& identity,
                               uint32_t dst_ip, uint16_t dst_port)
    : _udp(udp),
      _dst_ip(dst_ip),
      _dst_port(dst_port)
{
    const size_t uid_len = std::min(std::strlen(identity.uid),
                                    static_cast<size_t>(UID_SIZE));
    std::memcpy(_uid_slot, identity.uid, uid_len);
}

void SimFrameOutput::write(const Strip& strip, float t_program)
{
    // The index advances even for dropped frames, so the receiver can
    // tell a drop from a pause.
    const uint32_t frame_index = _frame_index++;

    if (!_udp.is_bound() && !_udp.bind(0)) return;

    uint8_t pkt[FRAME_PREVIEW_HEADER_BYTES + MAX_STRIP_PIXELS * sizeof(rgb_t)];
    WireWriter w(pkt, sizeof(pkt));
    w.write_bytes(_uid_slot, sizeof(_uid_slot));
    w.write_u32(frame_index);
    w.write_f32(t_program);
    w.write_bytes(strip.bytes(), strip.byte_size());

    if (!w.ok() || !_udp.send(pkt, w.bytes_written(), _dst_ip, _dst_port)) {
        _udp.close();  // reopen on the next write
    }
}
