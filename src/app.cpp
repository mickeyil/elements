#include "app.h"

#include "frame_output.h"
#include "hardware_profile_store.h"
#include "network_interface.h"
#include "platform_clock.h"
#include "system_platform.h"

App::App(NetworkInterface& network,
         DiscoveryClient&  discovery,
         TcpTransport&     tcp,
         UdpTransport&     sync_udp,
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
      _sync(sync_udp, _clock, identity)
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
}

void App::tick()
{
    _network.poll();
    _link.poll();

    // The reboot side effect is deferred to here so the ACK reaches
    // the controller first; the link has flushed it by the time its
    // poll() returns.
    if (_ctx.reboot_requested) {
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
        _status.mode = DeviceMode::AttachedControlled;
        return;
    }

    // Placeholder detach policy: drop everything and go dark. With no
    // profile nothing was ever shown and the output is not set up.
    _status.mode = DeviceMode::DetachedBlank;
    _ctx.local_program_loaded = false;
    _playback.reset_for_detach();
    if (_playback.has_hardware_profile()) {
        _playback.render_black_frame();
        write_frame_();
    }
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
