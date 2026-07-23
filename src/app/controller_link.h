#pragma once

#include <cstdint>

#include "app/command_processor.h"
#include "app/link_protocol.h"
#include "platform/tcp_transport.h"

class NetworkInterface;
struct DeviceIdentity;
class DiscoveryClient;
class CommandHandler;

// Liveness deadline: drop the link when no message has been handled
// for this long. Twice the ping interval, so one lost ping does not
// drop the connection.
constexpr int64_t PING_TIMEOUT_MS = 2 * PING_INTERVAL_MS;

// Minimum wait between connect attempts: twice the worst-case block,
// so even a half-dead controller (answers discovery, ignores TCP)
// cannot eat more than half the loop's time.
constexpr int64_t CONNECT_RETRY_INTERVAL_MS = 2 * TcpTransport::CONNECT_TIMEOUT_MS;

// ControllerLink's connectivity status: looking for a controller, or
// attached to one.
enum class LinkState : uint8_t {
    Discovering,  // no controller yet; searching
    Ready,        // connected and registered, serving commands
};

// The device's command connection to the controller: finds one via
// discovery, connects and registers, then serves inbound commands
// until the controller goes away, and starts over. The App polls it
// every tick and reads is_ready() to know whether it is attached.

class ControllerLink
{
public:
    ControllerLink(NetworkInterface& network,
                   DiscoveryClient&  discovery,
                   TcpTransport&     tcp,
                   const DeviceIdentity& identity,
                   CommandHandler&   handler);

    // Single per-tick entry point. Discovers and connects while
    // detached; runs the processor and the liveness deadline while
    // attached.
    void poll();

    // Bounded blocking flush of a pending ACK tail, for the App's
    // reboot path only: the device is about to go down, so blocking
    // is harmless and the ACK deserves a last chance to get out.
    // Loops until the tail drains, the link faults, or deadline_us
    // passes. Steady-state code never blocks like this.
    void drain_tx(int64_t deadline_us);

    // Is the link fully attached: TCP up, REGISTER written, liveness
    // deadline not expired.
    bool is_ready() const { return _state == LinkState::Ready; }

    // Controller IPv4 address in network byte order; 0 unless
    // is_ready().
    uint32_t controller_ip_addr() const { return _controller_ip; }

private:
    // Connect to the most recent OFFER and write REGISTER, all in
    // this tick.
    void try_connect_();

    // Write the REGISTER message (uid, boot_token, protocol_version).
    bool send_register_();

    // The one teardown path; safe to call in any state.
    void drop_link_();

    NetworkInterface& _network;
    DiscoveryClient&  _discovery;  // passed in so the sim can use unicast
    TcpTransport&     _tcp;
    const DeviceIdentity& _identity;
    CommandProcessor  _processor;

    LinkState _state = LinkState::Discovering;
    uint32_t _controller_ip = 0;     // 0 = not attached
    int64_t  _last_activity_us = 0;  // last handled message (or attach time)
    int64_t  _last_connect_us = 0;   // 0 = connect immediately
};
