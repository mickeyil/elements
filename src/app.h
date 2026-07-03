#pragma once

#include <cstdint>

#include "animation_store.h"
#include "app_context.h"
#include "clock_sync_client.h"
#include "command_handler.h"
#include "controller_link.h"
#include "device_status.h"
#include "log_sender.h"
#include "playback.h"
#include "synced_clock.h"

class DiscoveryClient;
class FrameOutput;
class KeyValueStore;
class NetworkInterface;
class SystemPlatform;
class UdpTransport;
struct DeviceIdentity;

// The shared application logic, used as is by both the firmware and
// the sim. Drives the whole device: controller discovery, command
// serving, clock sync, playback, frame output. The platform entry
// point hands in its concrete pieces, calls begin() once, then
// tick() forever.

class App
{
public:
    // The parameters are the platform-dependent pieces; everything
    // shared is owned inside.
    App(NetworkInterface& network,
        DiscoveryClient&  discovery,
        TcpTransport&     tcp,
        UdpTransport&     sync_udp,
        UdpTransport&     log_udp,
        FileStore&        files,
        KeyValueStore&    profile_kv,
        SystemPlatform&   system,
        FrameOutput&      output,
        const DeviceIdentity& identity);

    // One-time startup: bring the network up and apply the stored
    // hardware profile, if any.
    void begin();

    // One pass of the device loop. Non-blocking beyond the transport
    // timeouts.
    void tick();

    // Is the controller link attached and serving commands?
    bool is_attached() const { return _link.is_ready(); }

    const DeviceStatus& status() const { return _status; }

private:
    // Attach/detach policy: mirror the link state into status.mode. On detach
    // (and at boot) resume the stored background if one is installed and
    // playable, otherwise blank the strip. Grace hold is an open design item.
    void update_mode_();

    // Reset playback and try to start stored animation order 0 as the local
    // background. Sets DetachedBackground and returns true on success; on
    // failure leaves playback reset and returns false (the caller decides
    // whether to blank). Shared by boot (begin) and detach.
    bool try_start_background_();

    // Render frames at the loaded program's fps and write them to the
    // frame output; restart local animations on Ended.
    void drive_playback_();

    // Write the current strip to the frame output.
    void write_frame_();

    NetworkInterface& _network;
    FrameOutput&      _output;

    SyncedClock     _clock;
    DeviceStatus    _status;
    Playback        _playback;
    AnimationStore  _animations;
    AppContext      _ctx;
    CommandHandler  _handler;
    ControllerLink  _link;
    ClockSyncClient _sync;
    LogSender       _log_sender;

    bool    _link_was_ready    = false;
    int64_t _next_frame_due_us = 0;  // 0 = render immediately
};
