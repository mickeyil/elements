#include "app.h"

#include "frame_output.h"
#include "hardware_profile_store.h"
#include "local_animation.h"
#include "network_interface.h"
#include "platform_clock.h"
#include "slog.h"
#include "system_platform.h"

namespace {

// How long a pending reboot ACK may hold the reboot back. Writes are
// non-blocking, so an ACK the socket refused stays queued; give it a
// bounded last chance to reach the controller before the device dies.
constexpr int64_t REBOOT_ACK_DRAIN_TIMEOUT_MS = 500;

}  // namespace

App::App(NetworkInterface& network,
         DiscoveryClient&  discovery,
         TcpTransport&     tcp,
         UdpTransport&     sync_udp,
         UdpTransport&     log_udp,
         FileStore&        files,
         KeyValueStore&    profile_kv,
         SystemPlatform&   system,
         FrameOutput&      output,
         const DeviceIdentity& identity)
    : _network(network),
      _output(output),
      _playback(_clock),
      _animations(files),
      _ctx{_playback, _animations, profile_kv, _status, system},
      _handler(_ctx),
      _link(network, discovery, tcp, identity, _handler),
      _sync(sync_udp, _clock, identity),
      _log_sender(log_udp, discovery, identity)
{
}

void App::begin()
{
    _network.begin();

    HardwareProfile profile;
    if (load_hardware_profile(_ctx.profile_kv, profile) &&
        _playback.apply_hardware_profile(profile)) {
        _output.apply_profile(profile);
        _status.flags |= STATUS_FLAG_PROFILE_PRESENT;
    }

    // Boot detached: a device that powers up with no controller resumes its
    // installed background, so it shows something on its own. A controller
    // attaching later takes over (update_mode_). With no background installed
    // this leaves the strip as-is (DetachedBlank) until a controller arrives.
    try_start_background_();
}

void App::tick()
{
    _network.poll();
    _link.poll();

    // Ship after the link poll so records from this tick's command
    // handling go out the same tick; before the reboot check so a
    // reboot's own logs get one send window.
    _log_sender.tick(_link.is_ready());

    // The reboot side effect is deferred to here so the ACK reaches
    // the controller first: drain any unsent tail (bounded), then go
    // down. Past the deadline the ACK is abandoned; the controller
    // sees the connection drop and a fresh boot_token either way.
    if (_ctx.reboot_requested) {
        _link.drain_tx(now_us() + REBOOT_ACK_DRAIN_TIMEOUT_MS * 1000);
        _ctx.system.reboot();
        return;  // real reboots never get here; test fakes do
    }

    _sync.set_controller(_link.controller_ip_addr());
    _sync.poll();

    update_mode_();
    drive_playback_();
}

void App::update_mode_()
{
    const bool ready = _link.is_ready();
    if (ready == _link_was_ready) return;
    _link_was_ready = ready;

    if (ready) {
        // The controller owns playback now. Drop any local background so it
        // stops animating and a later controller program can't inherit its
        // loop flag; the reconciler will load/start its own program over this.
        slog_info("attached to controller");
        _playback.reset_for_detach();
        _ctx.local_program_loaded = false;
        _status.mode = DeviceMode::AttachedControlled;
        return;
    }

    slog_info("detached from controller");

    // Detached: resume the stored background if one is installed and playable.
    if (try_start_background_()) {
        return;
    }

    // No background to fall back to: go dark. With no profile nothing was ever
    // shown and the output is not set up. (try_start_background_ already reset
    // playback and cleared the local flag.)
    _status.mode = DeviceMode::DetachedBlank;
    if (_playback.has_hardware_profile()) {
        _playback.render_black_frame();
        write_frame_();
    }
}

bool App::try_start_background_()
{
    _playback.reset_for_detach();
    _ctx.local_program_loaded = false;
    if (play_stored_animation(_ctx, 0).ok()) {
        _status.mode = DeviceMode::DetachedBackground;
        return true;
    }
    return false;
}

void App::drive_playback_()
{
    if (_playback.state() != DeviceState::PLAYING) {
        _next_frame_due_us = 0;
        return;
    }

    const uint8_t fps = _playback.target_fps();
    if (fps == 0) return;
    const int64_t frame_interval_us = 1'000'000 / fps;

    const int64_t now = now_us();
    if (_next_frame_due_us == 0) {
        _next_frame_due_us = now;
    }
    if (now < _next_frame_due_us) return;

    const RenderFrameResult result = _playback.render_next_frame();

    // Step the deadline one frame; after a stall, re-anchor to now so
    // missed frames are skipped instead of bursted.
    _next_frame_due_us += frame_interval_us;
    if (_next_frame_due_us <= now) {
        _next_frame_due_us = now + frame_interval_us;
    }

    if (result == RenderFrameResult::Unchanged) return;

    write_frame_();

    if (result == RenderFrameResult::Ended && _ctx.local_program_loaded) {
        // Local animations loop. Restarting is valid from ENDED, and
        // stored blobs are unsynced, so the anchor value is ignored.
        _playback.handle_start(0);
    }
}

void App::write_frame_()
{
    _output.write(_playback.strip(), _playback.current_t_program());
}
