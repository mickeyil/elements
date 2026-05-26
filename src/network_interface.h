#pragma once

// Network association change reported by the most recent poll().
// `none` is the usual case; the others fire on the tick the change is
// first observed.
enum class NetworkTransition {
    none,
    came_up,
    went_down,
};

// Is the device on a usable LAN? Same shape on firmware (ESP-side
// Wi-Fi) and host (always up). The App polls this each tick before
// link.poll() / sync.poll(); both downstream modules self-gate on
// is_up() rather than the App wrapping calls in a guard, so they can
// reset stale state on Wi-Fi drops.
//
// Configuration (SSID, password, AP-mode fallback, host bind address)
// lives in the implementation's constructor; reconnect policy is
// internal to it.
//
// Implementations:
//   - EspNetworkInterface  (firmware, around WifiManager)
//   - HostNetworkInterface (sim/host, always up)

class NetworkInterface {
public:
    virtual ~NetworkInterface() = default;

    // One-time setup before the first poll().
    virtual void begin() = 0;

    // Make progress without blocking; returns any change since the last call.
    virtual NetworkTransition poll() = 0;

    // Is the device currently on a usable LAN?
    virtual bool is_up() const = 0;
};
