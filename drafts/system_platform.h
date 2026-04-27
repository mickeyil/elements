#pragma once

// Pluggable system-level operations. Owned by the firmware app loop /
// sim main, NOT by SystemHandler. The handler signals
// PollResult::reboot_requested; the outer loop calls reboot() after the
// ACK has been flushed to the controller.
//
// Two impls:
//   - EspSystemPlatform: deferred ESP.restart() with a short flush window.
//   - SimSystemPlatform: in-process simulated reboot -- close TCP, reset
//                        session/sync state, generate a new boot_token,
//                        resume discovery. Background storage persists.

namespace controller_link {

class SystemPlatform {
public:
    virtual ~SystemPlatform() = default;

    // Reboot the device. Sim version simulates an observable reboot: the
    // controller sees the connection drop and the device come back with a
    // new boot_token. ESP version restarts the chip.
    virtual void reboot() = 0;

    // TODO: a future "factory reset" command (clear NVS, clear background)
    // would land here next. Out of scope for v3.
};

}  // namespace controller_link
