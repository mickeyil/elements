#pragma once

#include <cstdint>

// Snapshot of the controller-supplied OFFER the device received over
// UDP discovery. Populated by the discovery service (outside this
// module) and consumed by:
//
//   - TcpTransport::connect()  reads controller_ipv4_be + tcp_port
//   - send_device_hello()      reads nonce
//
// Lifetime: set on OFFER, cleared on disconnect or simulated reboot.
// Most-recent-OFFER-wins; a fresh OFFER replaces any stale snapshot.
//
// Note: this struct deliberately holds only the bare minimum the link
// layer needs. There is no device_id (UID is the canonical identity)
// and no frame_port (sim-only frame UDP is configured outside the link
// layer, in sim CLI flags). See controller_link.md for the rationale.

struct ControllerOffer {
    uint32_t controller_ipv4_be = 0;
    uint16_t tcp_port = 0;
    uint32_t nonce = 0;
};
