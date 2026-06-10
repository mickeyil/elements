#pragma once

#include <cstdint>

class NetworkInterface;
struct DeviceIdentity;
class TcpTransport;
class DiscoveryClient;
class CommandProcessor;
class CommandHandler;

// Internal lifecycle stage. Used by poll() to decide what to do next.
//   NetworkDown : NetworkInterface::is_up() is false. Nothing to do.
//   Discovering : LAN is up; DiscoveryClient is polling for an OFFER.
//   Connecting  : OFFER received; TCP dial in progress (or about to be);
//                 REGISTER write happens in the same poll() tick once
//                 connect() returns true.
//   Ready       : TCP connected and REGISTER written. Processor is
//                 running; controller may send commands. There is
//                 deliberately no separate ACK for REGISTER; controller
//                 rejection shows up as the next read returning < 0,
//                 or as the liveness deadline firing (no handled message
//                 within PING_TIMEOUT). Either drops back to Discovering.
enum class LinkState {
    NetworkDown,
    Discovering,
    Connecting,
    Ready,
};

// "Have I discovered and opened a controller command link?"
//
// Owns: the outbound TCP dial, the REGISTER write, and the
// CommandProcessor that runs above the connected TCP socket. Drives a
// DiscoveryClient (sibling, passed in) to find the controller's
// address.
//
// Does not own: Wi-Fi (NetworkInterface is a sibling), clock sync
// (ClockSyncClient is a sibling, ticks alongside this one).
//
// Lifetime: one instance for the life of the program. poll() is the
// single per-tick entry point.
//
// Liveness. Any message the processor successfully handles is evidence
// the controller is alive; on PollResult::Handled the link bumps
// _last_activity_us (PollResult::Fault means drop the connection;
// the link owns all teardown). is_ready() returns false once
// (now - _last_activity_us) exceeds PING_TIMEOUT_US, and the link tears
// the socket down on the next tick. The controller is expected to send
// Ping on an interval so liveness keeps refreshing even when there are
// no other commands; Ping itself has no special signal path -- it is a
// normal handler that returns Ok, and the "handled" bool does the rest.
//
// Reboot signal. CommandHandler signals reboot by setting
// AppContext::reboot_requested. The App's outer loop reads that flag
// after link.poll() returns and calls SystemPlatform::reboot() once the
// ACK has been flushed. ControllerLink does not surface reboot itself.
//
// Polling contract (see drafts/controller_link.md): the App calls
// poll() unconditionally each tick. The link self-gates on
// network.is_up() internally; on the tick where the network drops it
// transitions to NetworkDown and tears down TCP; on the tick where the
// network comes back it resumes discovery. Callers do NOT wrap poll()
// in an is_up() guard.
//
// Construction wires in:
//   - NetworkInterface : tells the link whether the LAN is up. The
//                        link does nothing while it's down, and tears
//                        down on the transition down so it doesn't
//                        resume from stale state when it comes back.
//   - DiscoveryClient  : polled while no link is up; supplies the
//                        controller's IP and TCP port. Passed in (not
//                        private) so sim can configure a unicast
//                        dst_ip; UDP broadcast on loopback is unreliable.
//   - TcpTransport     : the command socket.
//   - DeviceIdentity   : UID / boot_token, sent in REGISTER.
//   - CommandHandler   : passed through to the internally-constructed
//                        CommandProcessor.

class ControllerLink {
public:
    ControllerLink(
        NetworkInterface& network,
        DiscoveryClient&  discovery,
        TcpTransport&     tcp,
        const DeviceIdentity& identity,
        CommandHandler&   handler
    );

    // Single per-tick entry point. Drives discovery / dial / handshake
    // / processor based on current state. Safe to call when network is
    // down (no-op).
    void poll();

    // True iff the link is fully attached: TCP up, REGISTER written,
    // and the liveness deadline (no handled message for > PING_TIMEOUT)
    // hasn't expired. The App keys mode transitions off this single bit.
    bool is_ready() const;

    // Controller IPv4 address in network byte order (ready for
    // sockaddr_in / WiFiClient). Valid only when is_ready(); returns 0
    // otherwise. ClockSyncClient reads this each tick to know where to
    // ping.
    uint32_t controller_ip_addr() const;

private:
    // Identify the device to the controller via the first message after
    // TCP connect (UID, boot_token, protocol_version). Returns false on
    // write error; the link then tears down and goes back to Discovering.
    bool send_identity_();

    LinkState _state = LinkState::NetworkDown;

    // Liveness deadline tracker. Set to now_us() on entry to Ready, and
    // again each tick the processor reports a handled message. Checked
    // each poll(): (now_us() - _last_activity_us) > PING_TIMEOUT_US
    // means drop.
    int64_t _last_activity_us = 0;
};
