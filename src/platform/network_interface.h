#pragma once

// Result of a poll() call: link came up, went down, or stayed the same.
enum class NetworkTransition {
    unchanged,
    link_up,
    link_down,
};

// Platform-agnostic network connection handle. Implemented per platform;
// the rest of the app uses only this interface.
//
// Implementations:
//   - EspNetworkInterface  (firmware, around WifiManager)
//   - HostNetworkInterface (sim/host, always up)

class NetworkInterface
{
public:
    virtual ~NetworkInterface() = default;

    // Initialize the network layer. Call once at startup before entering the poll loop. Non-blocking.
    virtual void begin() = 0;

    // Advance the network state machine. Non-blocking; returns the transition seen this tick.
    virtual NetworkTransition poll() = 0;

    // Is the device currently on a usable LAN?
    virtual bool is_up() const = 0;
};
