#pragma once

// Pluggable system-level operations. Owned by the firmware app loop /
// sim main, reachable from CommandHandler through AppContext but never
// called from the handler directly. The reboot handler sets
// ctx.reboot_requested; the outer loop calls system.reboot() once the
// ACK has been flushed to the controller. That ordering (ACK first,
// reboot after) is why the side effect is deferred to the loop and
// not run from inside handle().
//
// Two impls:
//   - EspSystemPlatform: ESP.restart() (firmware).
//   - SimSystemPlatform: _exit(SIM_REBOOT_EXIT_CODE) so the supervisor
//                        re-execs the binary. RAM is wiped naturally;
//                        boot_token regenerates through the normal
//                        startup path. File-backed local storage
//                        survives because the file does.

class SystemPlatform
{
public:
    virtual ~SystemPlatform() = default;

    // Reboot the device. ESP version restarts the chip; sim version
    // exits with the reboot sentinel so the supervisor relaunches.
    // Either way the controller sees the TCP connection drop and a
    // fresh REGISTER with a new boot_token.
    virtual void reboot() = 0;

    // TODO: a future "factory reset" command (clear NVS, clear local
    // animation store) would land here next. Out of scope for v3.
};
