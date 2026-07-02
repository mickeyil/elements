#include "log_shipper.h"

#include <cstring>

#include "device_identity.h"
#include "discovery.h"
#include "slog.h"
#include "udp_transport.h"

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

void put_u16_le(uint8_t* p, uint16_t v)
{
    p[0] = v & 0xFF;
    p[1] = (v >> 8) & 0xFF;
}

void put_u32_le(uint8_t* p, uint32_t v)
{
    p[0] = v & 0xFF;
    p[1] = (v >> 8) & 0xFF;
    p[2] = (v >> 16) & 0xFF;
    p[3] = (v >> 24) & 0xFF;
}

}  // namespace

LogShipper::LogShipper(UdpTransport& udp, const DiscoveryClient& discovery,
                       const DeviceIdentity& identity)
    : _udp(udp), _discovery(discovery), _identity(identity)
{
}

void LogShipper::tick(bool link_ready)
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
        put_u16_le(pkt, LOG_MAGIC);
        pkt[2] = LOG_VERSION;
        std::memset(pkt + 3, 0, UID_SIZE);
        std::memcpy(pkt + 3, _identity.uid,
                    strnlen(_identity.uid, UID_SIZE));
        put_u32_le(pkt + 3 + UID_SIZE, _identity.boot_token);
        put_u32_le(pkt + 3 + UID_SIZE + 4, rec.seq);
        put_u32_le(pkt + 3 + UID_SIZE + 8, rec.uptime_ms);
        pkt[3 + UID_SIZE + 12] = rec.level;

        const size_t text_len = strnlen(rec.text, sizeof(rec.text));
        std::memcpy(pkt + LOG_HEADER_BYTES, rec.text, text_len);

        if (!_udp.send(pkt, LOG_HEADER_BYTES + text_len, ip, LOG_PORT)) {
            return;   // keep the record; try again next tick
        }
        slog_pop();
    }
}
