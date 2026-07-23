#include "app/log_sender.h"

#include <cstring>

#include "platform/device_identity.h"
#include "app/discovery.h"
#include "app/slog.h"
#include "platform/udp_transport.h"
#include "app/wire_writer.h"

namespace {

constexpr uint16_t LOG_PORT = 6044;

// Filters unrelated traffic on the port; version starts the format's
// growth story.
constexpr uint16_t LOG_MAGIC   = 0xD16C;
constexpr uint8_t  LOG_VERSION = 1;

// magic(2) version(1) uid(16) boot_token(4) seq(4) uptime_ms(4) level(1);
// the text fills the rest of the datagram.
constexpr size_t LOG_HEADER_BYTES = 2 + 1 + UID_SIZE + 4 + 4 + 4 + 1;

// Cap on datagrams per tick so a burst cannot stretch a frame.
constexpr int MAX_SENDS_PER_TICK = 4;

}  // namespace

LogSender::LogSender(UdpTransport& udp, const DiscoveryClient& discovery,
                       const DeviceIdentity& identity)
    : _udp(udp), _discovery(discovery), _identity(identity)
{
}

void LogSender::tick(bool link_ready)
{
    // Discovery freshness goes stale while the link is up (discovery
    // is not polled then), so a ready link stands in as the liveness
    // signal.
    if (!link_ready && !_discovery.has_fresh_offer()) return;

    const uint32_t ip = _discovery.controller_ip();
    if (ip == 0) return;

    if (!_udp.is_bound() && !_udp.bind(0)) return;

    for (int i = 0; i < MAX_SENDS_PER_TICK; ++i) {
        SlogRecord rec;
        if (!slog_peek(rec)) return;

        uint8_t pkt[LOG_HEADER_BYTES + SLOG_TEXT_CAP];
        WireWriter w(pkt, sizeof(pkt));
        w.write_u16(LOG_MAGIC);
        w.write_u8(LOG_VERSION);
        uint8_t uid_slot[UID_SIZE] = {};
        std::memcpy(uid_slot, _identity.uid,
                    strnlen(_identity.uid, UID_SIZE));
        w.write_bytes(uid_slot, sizeof(uid_slot));
        w.write_u32(_identity.boot_token);
        w.write_u32(rec.seq);
        w.write_u32(rec.uptime_ms);
        w.write_u8(rec.level);
        w.write_bytes(reinterpret_cast<const uint8_t*>(rec.text),
                      strnlen(rec.text, sizeof(rec.text)));
        if (!w.ok()) return;   // cannot happen with these sizes

        if (!_udp.send(pkt, w.bytes_written(), ip, LOG_PORT)) {
            return;   // keep the record; try again next tick
        }
        slog_pop();
    }
}
