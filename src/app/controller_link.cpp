#include "app/controller_link.h"

#include <algorithm>
#include <cstring>

#include "platform/device_identity.h"
#include "app/discovery.h"
#include "app/link_protocol.h"
#include "platform/network_interface.h"
#include "platform/platform_clock.h"
#include "platform/tcp_transport.h"
#include "app/wire_writer.h"

namespace {

// REGISTER: length u32 LE | opcode u8 | uid 16B | boot_token u32 | version u8.
constexpr size_t REGISTER_PAYLOAD_BYTES = UID_SIZE + 4 + 1;
constexpr size_t REGISTER_MSG_BYTES = 4 + 1 + REGISTER_PAYLOAD_BYTES;

}  // namespace

ControllerLink::ControllerLink(NetworkInterface& network,
                               DiscoveryClient&  discovery,
                               TcpTransport&     tcp,
                               const DeviceIdentity& identity,
                               CommandHandler&   handler)
    : _network(network),
      _discovery(discovery),
      _tcp(tcp),
      _identity(identity),
      _processor(tcp, handler)
{
}

void ControllerLink::poll()
{
    if (!_network.is_up()) {
        drop_link_();
        return;
    }

    if (_state == LinkState::Ready) {
        if (!_tcp.is_connected()) {
            drop_link_();
            return;
        }

        const PollResult result = _processor.poll();
        if (result == PollResult::Fault) {
            drop_link_();
            return;
        }
        if (result == PollResult::Handled) {
            _last_activity_us = now_us();
        }
        if (now_us() - _last_activity_us > PING_TIMEOUT_MS * 1000) {
            drop_link_();
        }
        return;
    }

    _discovery.poll();
    try_connect_();
}

void ControllerLink::try_connect_()
{
    // Only connect to a controller that is still announcing itself;
    // connect() to a dead address blocks for its full timeout.
    if (!_discovery.has_fresh_offer()) return;

    const uint32_t ip = _discovery.controller_ip();
    const uint16_t port = _discovery.tcp_port();
    if (ip == 0 || port == 0) return;

    const int64_t now = now_us();
    if (_last_connect_us != 0 &&
        now - _last_connect_us < CONNECT_RETRY_INTERVAL_MS * 1000) {
        return;
    }
    _last_connect_us = now;

    if (!_tcp.connect(ip, port)) return;

    _processor.reset_stream();
    if (!send_register_()) {
        _tcp.disconnect();
        return;
    }

    _controller_ip = ip;
    _last_activity_us = now_us();
    _state = LinkState::Ready;
}

bool ControllerLink::send_register_()
{
    uint8_t msg[REGISTER_MSG_BYTES];
    WireWriter w(msg, sizeof(msg));
    w.write_u32(static_cast<uint32_t>(1 + REGISTER_PAYLOAD_BYTES));
    w.write_u8(CMD_REGISTER);

    uint8_t uid_slot[UID_SIZE] = {};
    const size_t uid_len = std::min(std::strlen(_identity.uid),
                                    static_cast<size_t>(UID_SIZE));
    std::memcpy(uid_slot, _identity.uid, uid_len);
    w.write_bytes(uid_slot, sizeof(uid_slot));
    w.write_u32(_identity.boot_token);
    w.write_u8(PROTOCOL_VERSION);

    // A freshly connected socket has an empty send buffer, so these
    // few bytes go whole or something is wrong with the connection;
    // a short write is treated as a failed connect and retried.
    return w.ok() &&
           _tcp.write(msg, w.bytes_written()) ==
               static_cast<int>(w.bytes_written());
}

void ControllerLink::drain_tx(int64_t deadline_us)
{
    while (_state == LinkState::Ready && _processor.tx_pending()) {
        if (!_processor.flush_tx()) {
            drop_link_();
            return;
        }
        if (now_us() >= deadline_us) return;
    }
}

void ControllerLink::drop_link_()
{
    _tcp.disconnect();
    _processor.reset_stream();
    _controller_ip = 0;
    _last_connect_us = 0;   // reconnect promptly once prerequisites return
    _state = LinkState::Discovering;
}
