#pragma once

#include <cstdint>

#include "register.h"

class NetworkInterface;
class TcpTransport;
class DiscoveryClient;
class CommandParser;
class SessionHandler;
class PlaybackHandler;
class StorageHandler;
class StatusHandler;
class SystemHandler;

// Internal lifecycle stage. Used by poll() to decide what to do next;
// not exposed on the public surface (no state() accessor yet).
//   NetworkDown : NetworkInterface::is_up() is false. Nothing to do.
//   Discovering : LAN is up; DiscoveryClient is polling for an OFFER.
//   Connecting  : OFFER received; TCP dial in progress (or about to be);
//                 REGISTER write happens in the same poll() tick
//                 once connect() returns true, so no observable
//                 "handshaking" sub-state.
//   Ready       : TCP connected and REGISTER written. Parser is
//                 running; controller may send commands. There is
//                 deliberately no separate ACK for REGISTER;
//                 controller rejection shows up as the next read
//                 returning < 0, which drops back to Discovering.
enum class LinkState {
    NetworkDown,
    Discovering,
    Connecting,
    Ready,
};

// "Have I discovered and opened a controller command link?"
//
// Owns: the outbound TCP dial, the REGISTER write, and the
// CommandParser that runs above the connected TCP socket. Drives a
// DiscoveryClient (sibling, passed in) to find the controller's
// address.
//
// Does not own: Wi-Fi (NetworkInterface is a sibling), clock sync
// (ClockSyncClient is a sibling, ticks alongside this one).
//
// Lifetime: one instance for the life of the program. poll() is the
// single per-tick entry point; it advances whichever stage the link is
// in.
//
// Polling contract (see drafts/controller_link.md): the App calls
// poll() unconditionally each tick. The link self-gates on
// network.is_up() internally; on the tick where the network drops
// it transitions to NetworkDown and tears down TCP; on the tick
// where the network comes back it resumes discovery. Callers do
// NOT wrap poll() in an is_up() guard.
//
// Downstream consumers (ClockSyncClient, App mode logic) read
// is_ready() to decide their own behavior.
//
// Construction wires in:
//   - NetworkInterface : checked each poll() to decide whether to do
//                        anything at all.
//   - DiscoveryClient  : polled while no link is up; supplies the
//                        controller's IP and TCP port.
//   - TcpTransport     : the command socket.
//   - DeviceIdentity   : UID / boot_token, sent in REGISTER.
//   - The five handlers: passed through to the internally-constructed
//                        CommandParser.

class ControllerLink {
public:
    ControllerLink(
        NetworkInterface& network,
        DiscoveryClient&  discovery,
        TcpTransport&     tcp,
        const DeviceIdentity& identity,
        SessionHandler&   session,
        PlaybackHandler&  playback,
        StorageHandler&   storage,
        StatusHandler&    status,
        SystemHandler&    system
    );

    // Single per-tick entry point. Drives discovery / dial / handshake
    // / parser based on current state. Safe to call when network is
    // down (no-op). Surfaces reboot intent to the caller via the App's
    // own channel (today that's a poll-result on the parser; the link
    // forwards it).
    //
    // TODO: pin the reboot signal path. Either ControllerLink::poll()
    // returns a small result struct, or the App reads a getter after
    // poll(). Same content either way.
    void poll();

    // True iff the link is fully attached: TCP up and REGISTER
    // written. The app keys mode transitions off this single bit.
    bool is_ready() const;

    // Controller IPv4 address in network byte order (ready for
    // sockaddr_in / WiFiClient). Valid only when is_ready(); returns 0
    // otherwise. ClockSyncClient reads this each tick to know where to
    // ping.
    uint32_t controller_ip_addr() const;

private:
    LinkState _state = LinkState::NetworkDown;
    // Cached controller endpoint at connect time, parser, etc.;
    // private members. Not part of the public surface.
};
