"""The v3 controller service: the orchestrator that binds the device hub and
the session, owns the program library, and translates between the two worlds.

Client commands (load/play/pause/resume/stop and library queries) become
session verbs; session state and events become messages the server pushes to
clients. This layer holds no sockets of its own; server.py wraps it in the
unix-socket plumbing, and tests drive handle_cmd()/tick_once() directly.

One clock backs both halves: the session stamps program_start_us with it and
the hub's sync server hands the same domain to devices, so they share a
canonical time.

This is the control core: it speaks load/play/pause/resume/stop, device
add/edit/remove, plus library queries and shutdown, and it publishes session
state, events, and assembled preview frames. The web client's full-state and
status vocabulary is still the v2 shape (the web pass).
"""

import logging
import time

from pathlib import Path

from . import config as config_mod
from . import config_edit
from . import ota
from . import wire
from .controller_protocol import encode_frame, encode_json
from .device_status import DeviceStatusPoller
from .hub import DeviceHub
from .library import ArtifactCache, ProgramLibrary
from .session import (
    Intent,
    MemberAttached,
    MemberCommandFailed,
    MemberDetached,
    Session,
)

log = logging.getLogger(__name__)

# How often the firmware image and its version sidecar are re-checked on
# disk; a fresh `pio run` shows up in the UI within this long.
FIRMWARE_CHECK_INTERVAL_US = 1_000_000

# Transfer progress changes every chunk; republishing the full state that
# often would flood the UI, so progress refreshes at most this often.
# Phase changes (start, done, failed) publish immediately.
OTA_PROGRESS_INTERVAL_US = 500_000


def _doc_label(doc, target_uid):
    """The stored label of a device entry, or None if it has none / is absent."""
    for d in doc.get('devices', []):
        if isinstance(d, dict) and d.get('device_uid') == target_uid:
            return d.get('label')
    return None


def _bg_refused(step, status):
    """Human-readable reason for a background step a device refused."""
    return f'{step} refused: {wire.ACK_STATUS_NAMES.get(status, status)}'


class ControllerService:
    """Binds hub + session + library; the socket-free heart of the server.

    Construct from a Config. A hub and clock are built from the config ports
    unless injected (tests pass a fake hub and a fake clock); a library is
    built over the configured animations directory unless injected.
    """

    def __init__(self, config, *, config_path=None, hub=None, clock_us=None,
                 library=None, ota_factory=None):
        if clock_us is None:
            clock_us = lambda: time.monotonic_ns() // 1000
        self._config = config
        self._config_path = config_path
        self._clock_us = clock_us
        if hub is None:
            hub = DeviceHub(discovery_port=config.discovery_port,
                            link_port=config.link_port,
                            frame_port=config.frame_port,
                            sync_port=config.sync_port,
                            log_port=config.log_port,
                            clock_us=clock_us)
        self._hub = hub
        self._session = Session(hub, config.devices, clock_us)
        self._device_status = DeviceStatusPoller(hub, clock_us)
        self._library = library if library is not None else ProgramLibrary(
            config.animations_dir or config_mod.DEFAULT_ANIMATIONS_PATH)
        self._artifact_cache = ArtifactCache()

        self._loaded_program_id = None
        self._last_state = None      # last published state dict (change gate)
        self._catalog_dirty = True   # send the catalog on the next tick
        self._shutdown = False
        # In-flight / last background install per device uid: the set_background
        # command stores a program as a device's local fallback animation. Each
        # value is {'program_id', 'phase', 'error'}; surfaced in the state dict.
        self._bg_pushes = {}

        # Firmware update: at most one transfer at a time, run on its own
        # thread by an ota.FirmwareUpdate (tests inject a fake factory).
        # _ota_view is the runner snapshot last chosen for publishing (see
        # _poll_firmware_update); _ota_logged_phase makes the outcome log
        # exactly once per transfer.
        self._ota_factory = ota_factory or ota.FirmwareUpdate
        self._ota = None
        self._ota_view = None
        self._ota_view_us = 0
        self._ota_logged_phase = None
        # The image on disk and the version its build stamped beside it
        # (firmware.version, from tools/pio_version.py); see
        # _refresh_firmware_image().
        self._firmware_image = config_mod.resolve_firmware_image(config)
        self._firmware_checked_us = None
        self._firmware_sidecar_stat = None
        self._firmware_available_version = None
        self._firmware_image_present = False
        self._refresh_firmware_image()

    @property
    def should_shutdown(self):
        return self._shutdown

    def close(self):
        self._hub.close()

    # -- client-facing surface (server.py drives these) ---------------------

    def set_preview_enabled(self, enabled):
        """Gate preview-frame assembly. The server calls this when its frame
        subscriber appears or leaves, so the controller does no preview work
        while nobody is watching."""
        self._session.set_preview_enabled(enabled)

    def snapshot_messages(self):
        """The full state plus the program catalog, for a newly connected
        client. Returned as encoded controller-protocol messages."""
        return [encode_json(self._state_dict()),
                encode_json(self._catalog_dict())]

    def tick_once(self):
        """Advance the session one tick and collect what to publish: session
        events, the full state when it changed, the catalog when it changed,
        and any assembled preview frames. Returns (json_msgs, frame_msgs), both
        lists of encoded bytes."""
        events = self._session.tick()
        for ev in events:
            if isinstance(ev, (MemberAttached, MemberDetached)):
                self._device_status.forget(ev.uid)
        self._device_status.tick(self._session.attached_uids())
        json_msgs = [encode_json(self._event_dict(ev)) for ev in events]
        self._reap_orphaned_background()
        self._refresh_firmware_image()
        self._poll_firmware_update()

        state = self._state_dict()
        if state != self._last_state:
            self._last_state = state
            json_msgs.append(encode_json(state))

        if self._catalog_dirty:
            self._catalog_dirty = False
            json_msgs.append(encode_json(self._catalog_dict()))

        frame_msgs = [encode_frame(f.frame_index, f.t_rel, f.strips)
                      for f in self._session.drain_preview_frames()]
        return json_msgs, frame_msgs

    def handle_cmd(self, cmd):
        """Run one writer command and return its reply envelope. A refused
        verb or a bad request surfaces as ok=False with a message; the session
        validates before committing, so a rejected command changes nothing."""
        cmd_id = cmd.get('id')
        name = cmd.get('cmd')
        try:
            result = self._dispatch(name, cmd)
        except ValueError as e:
            return self._reply(cmd_id, ok=False, error=str(e))
        except Exception as e:
            log.exception('command %r failed', name)
            return self._reply(cmd_id, ok=False, error=str(e))
        return self._reply(cmd_id, ok=True, result=result)

    # -- command dispatch ---------------------------------------------------

    def _dispatch(self, name, cmd):
        if name == 'load':
            return self._cmd_load(cmd)
        if name == 'play':
            self._session.play()
            return {}
        if name == 'pause':
            self._session.pause()
            return {}
        if name == 'resume':
            self._session.resume()
            return {}
        if name == 'stop':
            self._session.stop()
            return {}
        if name == 'add_device':
            return self._cmd_add_device(cmd)
        if name == 'edit_device':
            return self._cmd_edit_device(cmd)
        if name == 'remove_device':
            return self._cmd_remove_device(cmd)
        if name == 'shutdown':
            self._shutdown = True
            return {}
        if name == 'get_state':
            return {'state': self._state_dict()}
        if name == 'list_programs':
            return {'programs': self._catalog_dict()['programs']}
        if name == 'rescan':
            self._library.rescan()
            self._catalog_dirty = True
            return {}
        if name == 'publish':
            return self._cmd_publish(cmd)
        if name == 'set_background':
            return self._cmd_set_background(cmd)
        if name == 'update_firmware':
            return self._cmd_update_firmware(cmd)
        raise ValueError(f'unknown command: {name!r}')

    def _cmd_load(self, cmd):
        program_id = cmd.get('program_id')
        if not isinstance(program_id, str) or not program_id:
            raise ValueError('load requires a program_id')
        entry = self._library.get(program_id)
        if entry is None:
            raise ValueError(f'unknown program: {program_id!r}')
        if entry.error is not None:
            raise ValueError(f'program {program_id!r} has errors: {entry.error}')
        manifest = self._resolve_manifest(entry)
        self._session.load(manifest)   # raises ValueError on routing/geometry
        self._loaded_program_id = program_id
        return {'program_id': program_id}

    def _cmd_publish(self, cmd):
        entry = self._library.publish(cmd.get('program_id'), cmd.get('source'))
        self._catalog_dirty = True
        return {'program_id': entry.program_id}

    # -- device editing -----------------------------------------------------

    def _cmd_add_device(self, cmd):
        # Adding a device is allowed even with a program loaded: the new member
        # parks DETACHED and joins only on the next load, so it cannot disturb
        # the running program. Edit/remove stay gated (they can touch a serving
        # member); see Session.add_device and _require_editable.
        doc = self._editable_doc()
        # device_type is inferred from the uid in v3; accept and ignore it.
        entry = config_edit.make_device_entry(
            device_uid=cmd.get('device_uid'), strip_id=cmd.get('strip_id'),
            length=cmd.get('length'), label=cmd.get('label'))
        config_edit.add_device(doc, entry)
        new_config = self._commit_config(doc)
        self._session.add_device(self._device_config(new_config, entry['device_uid']))
        return {'device_uid': entry['device_uid']}

    def _cmd_edit_device(self, cmd):
        self._require_editable()
        target = cmd.get('target_device_uid')
        doc = self._editable_doc()
        # Web edit forms omit label; preserve the stored one when absent, and
        # treat an explicit null as a request to clear it.
        label = cmd.get('label') if 'label' in cmd else _doc_label(doc, target)
        config_edit.edit_device(doc, target, device_uid=cmd.get('device_uid'),
                                strip_id=cmd.get('strip_id'),
                                length=cmd.get('length'), label=label)
        new_config = self._commit_config(doc)
        self._session.edit_device(
            target, self._device_config(new_config, cmd.get('device_uid')))
        return {'device_uid': cmd.get('device_uid')}

    def _cmd_remove_device(self, cmd):
        self._require_editable()
        uid = cmd.get('device_uid')
        doc = self._editable_doc()
        config_edit.remove_device(doc, uid)
        self._commit_config(doc)
        self._session.remove_device(uid)
        return {'device_uid': uid}

    def _require_editable(self):
        if not self._session.can_edit_devices():
            raise ValueError('devices can only be edited before a program is loaded')

    def _editable_doc(self):
        if self._config_path is None:
            raise ValueError('device edits require a server config file')
        return config_edit.load_config_doc(self._config_path)

    def _commit_config(self, doc):
        """Validate, persist, and adopt an edited config doc. Validation runs
        before the write, so a rejected edit leaves the file untouched."""
        new_config = config_mod.load_config_obj(doc)   # ConfigError is a ValueError
        config_mod.write_json_file_atomic(Path(self._config_path), doc)
        self._config = new_config
        return new_config

    @staticmethod
    def _device_config(config, uid):
        for dc in config.devices:
            if dc.device_uid == uid:
                return dc
        raise ValueError(f'device not found after edit: {uid!r}')

    # -- background install --------------------------------------------------

    _BG_TERMINAL = ('ready', 'failed')

    def _cmd_set_background(self, cmd):
        """Install a program as a device's local fallback background animation.

        Compiles the program for the device's strip, then stores it and makes it
        the device's sole local animation (STORE_ANIMATION + SET_ANIMATION_ORDER).
        With preview=true it also starts it locally now (PLAY_LOCAL_ANIMATION),
        which is what the device will do on its own once the detach-to-background
        firmware/App behaviour lands.

        Precondition: the device must already hold a hardware profile (e.g. from
        previewing the strip via load/play). SET_PROFILE is intentionally not
        sent here; without a profile the store still succeeds, but the background
        cannot start (preview, or the eventual detach playback, falls back to a
        blank strip).

        Validation is synchronous (a bad request fails the command immediately);
        the device commands are async, so their progress shows up as the device's
        'background' phase in the state dict: storing -> ordering -> [starting ->]
        ready, or failed."""
        uid = cmd.get('device_uid')
        program_id = cmd.get('program_id')
        preview = bool(cmd.get('preview', False))
        if not isinstance(uid, str) or not uid:
            raise ValueError('set_background requires a device_uid')
        if not isinstance(program_id, str) or not program_id:
            raise ValueError('set_background requires a program_id')

        dc = next((d for d in self._config.devices if d.device_uid == uid), None)
        if dc is None:
            raise ValueError(f'unknown configured device: {uid!r}')
        if not self._hub.is_connected(uid):
            raise ValueError(f'device {uid!r} is not connected')
        # Storing/ordering are harmless while a program runs, but a preview's
        # PLAY_LOCAL would hijack a device the session is actively driving.
        if preview and self._session.member(uid).target.intent is not Intent.DETACHED:
            raise ValueError(
                f'cannot preview on {uid!r} while it is serving a loaded program; '
                'stop the program first, or omit preview')

        existing = self._bg_pushes.get(uid)
        if existing is not None and existing['phase'] not in self._BG_TERMINAL:
            raise ValueError(f'a background install is already in progress for {uid!r}')

        entry = self._library.get(program_id)
        if entry is None:
            raise ValueError(f'unknown program: {program_id!r}')
        if entry.error is not None:
            raise ValueError(f'program {program_id!r} has errors: {entry.error}')
        manifest = self._resolve_manifest(entry)
        artifact = manifest.strips.get(dc.strip_id)
        if artifact is None:
            raise ValueError(
                f'program {program_id!r} has no strip for {dc.strip_id!r}')
        if manifest.requires_sync:
            raise ValueError(
                f'program {program_id!r} requires sync and cannot be a background; '
                'a background plays on the device alone, with no controller clock')

        # Encode every step up front so any wire-level rejection (name too long,
        # blob too large) fails the command synchronously rather than mid-chain.
        store_msg = wire.encode_store_animation(program_id, artifact.blob)
        order_msg = wire.encode_set_animation_order([program_id])
        play_msg = wire.encode_play_local_animation(0) if preview else None

        push = {'program_id': program_id, 'phase': 'storing', 'error': None}

        def on_store(ack):
            if ack.status != wire.ACK_OK:
                return self._bg_fail(uid, push, _bg_refused('store_animation', ack.status))
            push['phase'] = 'ordering'
            if not self._hub.send(uid, order_msg, on_order):
                self._bg_fail(uid, push, 'set_animation_order failed: device not connected')

        def on_order(ack):
            if ack.status != wire.ACK_OK:
                return self._bg_fail(uid, push, _bg_refused('set_animation_order', ack.status))
            if play_msg is None:
                push['phase'] = 'ready'
                return
            push['phase'] = 'starting'
            if not self._hub.send(uid, play_msg, on_play):
                self._bg_fail(uid, push, 'play_local_animation failed: device not connected')

        def on_play(ack):
            if ack.status != wire.ACK_OK:
                return self._bg_fail(uid, push, _bg_refused('play_local_animation', ack.status))
            push['phase'] = 'ready'

        self._bg_pushes[uid] = push
        if not self._hub.send(uid, store_msg, on_store):
            # is_connected raced us between the check and the send.
            del self._bg_pushes[uid]
            raise ValueError(f'device {uid!r} is not connected')

        log.info('set_background: installing %s on %s (preview=%s)',
                 program_id, uid, preview)
        return {'device_uid': uid, 'program_id': program_id, 'phase': push['phase']}

    def _bg_fail(self, uid, push, reason):
        push['phase'] = 'failed'
        push['error'] = reason
        log.warning('set_background: %s failed on %s: %s',
                    push['program_id'], uid, reason)

    def _reap_orphaned_background(self):
        """Fail any in-flight install whose device has dropped. The hub abandons
        a link's pending ACK callbacks when it closes, so without this a mid-
        install disconnect would wedge the device's slot as 'in progress' and
        reject every retry after it reconnects."""
        for uid, push in self._bg_pushes.items():
            if push['phase'] not in self._BG_TERMINAL and not self._hub.is_connected(uid):
                self._bg_fail(uid, push, 'device disconnected during install')

    # -- firmware update ----------------------------------------------------

    def _cmd_update_firmware(self, cmd):
        """Flash the built firmware image onto one ESP over ArduinoOTA.

        Manual by design: the operator picks the device and the moment. The
        target address is the source of the device's latest DISCOVER, so this
        works for any device that broadcasts, configured or not, linked or
        refused (an old protocol version is exactly when an update is needed).
        Validation is synchronous; the transfer runs in the background and
        its progress is the state dict's firmware.update."""
        uid = cmd.get('uid')
        if not isinstance(uid, str) or not uid:
            raise ValueError('update_firmware requires a uid')
        if uid.startswith('sim-'):
            raise ValueError('sim devices do not take firmware updates')
        if self._ota is not None and self._ota.snapshot()['phase'] not in ota.TERMINAL_PHASES:
            raise ValueError('a firmware update is already running')
        info = self._hub.device_info(uid)
        if info is None:
            raise ValueError(f'no known address for {uid!r}; it has not broadcast DISCOVER')
        image = self._firmware_image
        if not image.is_file():
            raise ValueError(f'firmware image not found: {image}')

        runner = self._ota_factory(uid=uid, device_ip=info.ip, image_path=image,
                                   listen_port=self._config.ota_port,
                                   password=self._config.ota_password)
        runner.start()
        self._ota = runner
        self._ota_logged_phase = None
        log.info('update_firmware: sending %s (version %s) to %s at %s; '
                 'device reports version %r',
                 image, self._firmware_available_version, uid, info.ip, info.version)
        self._poll_firmware_update(force=True)
        return {}

    def _poll_firmware_update(self, force=False):
        """Refresh the published view of the transfer and log its outcome once."""
        if self._ota is None:
            return
        snap = self._ota.snapshot()
        phase = snap['phase']
        if phase in ota.TERMINAL_PHASES and self._ota_logged_phase != phase:
            self._ota_logged_phase = phase
            if phase == ota.PHASE_DONE:
                log.info('update_firmware: %s accepted %d bytes; it reboots into '
                         'the new image', snap['uid'], snap['bytes_sent'])
            else:
                log.warning('update_firmware: %s failed after %d/%d bytes: %s',
                            snap['uid'], snap['bytes_sent'], snap['total_bytes'],
                            snap['error'])
        now = self._clock_us()
        view = self._ota_view
        if (force or view is None or view['phase'] != phase
                or now - self._ota_view_us >= OTA_PROGRESS_INTERVAL_US):
            self._ota_view = snap
            self._ota_view_us = now

    def _refresh_firmware_image(self):
        """Re-stat the image and its version sidecar, at most once per
        FIRMWARE_CHECK_INTERVAL_US; the sidecar is re-read only when its
        mtime or size changed."""
        now = self._clock_us()
        if (self._firmware_checked_us is not None
                and now - self._firmware_checked_us < FIRMWARE_CHECK_INTERVAL_US):
            return
        self._firmware_checked_us = now
        self._firmware_image_present = self._firmware_image.is_file()
        sidecar = self._firmware_image.parent / 'firmware.version'
        try:
            st = sidecar.stat()
            stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            stamp = None
        if stamp == self._firmware_sidecar_stat:
            return
        self._firmware_sidecar_stat = stamp
        version = None
        if stamp is not None:
            try:
                version = sidecar.read_text(encoding='ascii').strip() or None
            except (OSError, UnicodeDecodeError) as e:
                log.warning('firmware: cannot read %s: %s', sidecar, e)
        self._firmware_available_version = version

    # -- compile path -------------------------------------------------------

    def _resolve_manifest(self, entry):
        topology = self._topology_fingerprint()
        manifest = self._artifact_cache.get(entry.source_hash, topology)
        if manifest is None:
            manifest = self._compile(entry.source, entry.beat, entry.duration)
            self._artifact_cache.put(entry.source_hash, topology, manifest)
        return manifest

    def _compile(self, source, beat, duration):
        from elements.dsl import _builder, build_manifest
        _builder.reset()
        _builder.configured_strip_lengths = self._logical_strip_lengths()
        try:
            exec(source, {'__builtins__': __builtins__})
            return build_manifest(beat=beat, duration=duration)
        finally:
            _builder.reset()

    def _logical_strip_lengths(self):
        # config validation guarantees one length per strip_id.
        return {dc.strip_id: dc.length for dc in self._config.devices}

    def _topology_fingerprint(self):
        parts = sorted(self._logical_strip_lengths().items())
        return ';'.join(f'{sid}:{length}' for sid, length in parts)

    # -- message builders ---------------------------------------------------

    @staticmethod
    def _reply(cmd_id, *, ok, result=None, error=None):
        msg = {'type': 'reply', 'id': cmd_id, 'ok': ok}
        if ok:
            msg['result'] = result or {}
        else:
            msg['error'] = error or 'error'
        return msg

    def _state_dict(self):
        s = self._session
        manifest = s.manifest
        session = {
            'state': s.state.name.lower(),
            'session_id': s.session_id,
            'epoch': s.epoch,
            'program_id': self._loaded_program_id,
            'duration': manifest.duration if manifest is not None else None,
            'program_start_us': s.program_start_us,
            'cursor_us': s.cursor_us,
            # Program strip layout in manifest order: the order and lengths an
            # observer needs to split an assembled preview frame's rgb payload.
            'strips': ([{'strip_id': sid, 'length': art.length}
                        for sid, art in manifest.strips.items()]
                       if manifest is not None else []),
        }
        devices = []
        configured = set()
        for dc in self._config.devices:
            configured.add(dc.device_uid)
            m = s.member(dc.device_uid)
            report = self._device_status.report(dc.device_uid)
            info = self._hub.device_info(dc.device_uid)
            devices.append({
                'uid': dc.device_uid,
                'configured': True,
                'status': 'online' if m.attached else 'offline',
                'label': dc.label,
                'strip_id': dc.strip_id,
                'length': dc.length,
                'attached': m.attached,
                'serving': m.serving,
                'phase': m.phase.name.lower(),
                # What the session wants of this member, so a UI can tell a
                # parked late/reconnected device (target playing, phase loaded)
                # from one that is actually running, without inferring it.
                'target_intent': m.target.intent.name.lower(),
                'profile_state': m.profile_state.name.lower(),
                'last_refusal': list(m.last_refusal) if m.last_refusal else None,
                'blocked': list(m.blocked) if m.blocked else None,
                # Progress of a set_background install, or None if never run. A
                # copy, so in-place phase mutations by ACK callbacks don't also
                # mutate the cached _last_state and defeat the change gate.
                'background': (dict(push)
                               if (push := self._bg_pushes.get(dc.device_uid)) is not None
                               else None),
                # From the device's latest status reply; None until it answers
                # after attaching. Skew is how far the device clock had wandered
                # at its last sync round (0 until its second round).
                'clock_synced': report.clock_synced if report else None,
                'clock_skew_ms': report.clock_skew_us / 1000 if report else None,
                # From the device's latest DISCOVER, kept after it goes quiet;
                # None until one arrives. version is '' for firmware that
                # predates reporting it.
                'version': info.version if info else None,
                'ip': info.ip if info else None,
            })
        # Devices broadcasting DISCOVER but not in the config: a UID stub the
        # operator can configure. Configured devices win, so none appears twice.
        for uid in sorted(self._hub.discovered_uids() - configured):
            info = self._hub.device_info(uid)
            devices.append({'uid': uid, 'configured': False, 'status': 'discovered',
                            'version': info.version if info else None,
                            'ip': info.ip if info else None})
        firmware = {
            'available_version': self._firmware_available_version,
            'image_present': self._firmware_image_present,
            # snapshot() hands out a fresh dict, so the runner thread cannot
            # mutate what the change gate compares against.
            'update': self._ota_view,
        }
        return {'type': 'state', 'session': session, 'devices': devices,
                'firmware': firmware}

    def _catalog_dict(self):
        programs = [{
            'program_id': e.program_id,
            'beat': e.beat,
            'duration': e.duration,
            'strips': e.strips,
            'error': e.error,
        } for e in self._library.list_programs()]
        return {'type': 'catalog', 'programs': programs}

    def _event_dict(self, ev):
        if isinstance(ev, MemberAttached):
            return {'type': 'event', 'event': 'member_attached', 'uid': ev.uid,
                    'boot_token': ev.boot_token, 'rebooted': ev.rebooted}
        if isinstance(ev, MemberDetached):
            return {'type': 'event', 'event': 'member_detached', 'uid': ev.uid,
                    'reason': ev.reason}
        if isinstance(ev, MemberCommandFailed):
            return {'type': 'event', 'event': 'member_command_failed',
                    'uid': ev.uid, 'command': ev.command, 'status': ev.status}
        return {'type': 'event', 'event': 'unknown'}
