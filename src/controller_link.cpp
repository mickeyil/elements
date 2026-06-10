#include "controller_link.h"

#include <algorithm>
#include <cstring>

#include "device_identity.h"
#include "discovery.h"
#include "link_protocol.h"
#include "network_interface.h"
#include "platform_clock.h"
#include "tcp_transport.h"
#include "wire_writer.h"

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

    _processor.reset_buffer();
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

    return w.ok() && _tcp.write(msg, w.bytes_written());
}

void ControllerLink::drop_link_()
{
    _tcp.disconnect();
    _processor.reset_buffer();
    _controller_ip = 0;
    _last_connect_us = 0;   // reconnect promptly once prerequisites return
    _state = LinkState::Discovering;
}
