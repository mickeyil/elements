#pragma once

#include <cstdint>

#include "device_hello.h"

class SyncedClock;

namespace controller_link {

class UdpTransport;
class ControllerLink;

// Device-side feeder for SyncedClock. Owns the UDP socket on the sync
// port, the ping cadence, and (under the device-computes option) the
// small filter that turns RTT samples into an offset estimate. Calls
// SyncedClock::apply_sync_offset() with each accepted lease.
//
// SyncedClock itself is the passive container -- this client writes to
// it; Playback (and others) read from it. There is deliberately no
// wrapper: SyncedClock and ClockSyncClient are siblings.
//
// Lifecycle is gated entirely on ControllerLink::is_ready(). When the
// link drops, poll() resets the filter window so a stale pre-detach
// offset doesn't blend with a fresh post-attach measurement.
//
// Sim parity: same code on ESP and sim. Sim runs against a controller
// process on the same host, so the measured offset is ~0; that's not
// faked, it's the truth. The sync wire is exercised on every sim run.
//
// See drafts/synced_clock.md for the wire flow and the open
// device-computes vs controller-computes decision.

class ClockSyncClient {
public:
    ClockSyncClient(
        UdpTransport&         udp,
        const ControllerLink& link,
        SyncedClock&          clock,
        const DeviceIdentity& identity
    );

    // Single per-tick entry point. No-op when !link.is_ready();
    // resets internal filter state on the down-edge.
    void poll();
};

}  // namespace controller_link
