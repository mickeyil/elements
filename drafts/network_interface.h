#pragma once

namespace controller_link {

// Reports a Wi-Fi / LAN association change observed during the most
// recent poll(). `none` is the usual return; `came_up` and
// `went_down` fire on the tick the change is first seen.
enum class NetworkTransition {
    none,
    came_up,
    went_down,
};

// "Am I on a usable LAN?" Two impls live elsewhere:
//   - EspNetworkInterface  (wraps Arduino WiFi on firmware -- SSID,
//                           reconnect policy, signal monitoring)
//   - HostNetworkInterface (sim/host -- always reports up)
//
// Reconnect is INTERNAL to the impl. The ESP impl already runs
// WiFi.setAutoReconnect(true) and keeps watching; the host impl has
// nothing to reconnect. Callers do not get a public reconnect() --
// micromanaging reconnect timing isn't theirs to do.
//
// poll() is the single per-tick entry point. The App calls it
// unconditionally each loop, then calls link.poll() and sync.poll()
// also unconditionally -- those modules self-gate on is_up() /
// is_ready() internally. See drafts/controller_link.md § "The polling
// contract" for the rationale (gating in the App on is_up() would
// strand stale sync state when Wi-Fi drops).
//
// Configuration (SSID, password, AP-mode fallback, host bind address)
// lives in the impl's constructor and is not part of this interface.

class NetworkInterface {
public:
    virtual ~NetworkInterface() = default;

    // One-time setup. Called once at startup, before poll().
    virtual void begin() = 0;

    // Make whatever progress is available without blocking. Returns
    // any transition observed since the last call.
    virtual NetworkTransition poll() = 0;

    // True iff the device currently has a usable LAN association.
    // Steady-state. Flips alongside the transition returned by poll().
    virtual bool is_up() const = 0;
};

}  // namespace controller_link
