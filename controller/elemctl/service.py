"""The v3 controller service: the orchestrator that binds the device hub and
the session, owns the program library, and translates between the two worlds.

Client commands (load/play/pause/resume/stop and library queries) become
session verbs; session state and events become messages the server pushes to
clients. This layer holds no sockets of its own; server.py wraps it in the
unix-socket plumbing, and tests drive handle_cmd()/tick_once() directly.

One clock backs both halves: the session stamps program_start_us with it and
the hub's sync server hands the same domain to devices, so they share a
canonical time.

This is the control core: it speaks load/play/pause/resume/stop plus library
queries and shutdown, and it publishes session state, events, and assembled
preview frames. The device-management and status vocabularies the v2 clients
still use are not here yet (the TUI/web pass).
"""

import logging
import time

from . import config as config_mod
from .controller_protocol import encode_frame, encode_json
from .hub import DeviceHub
from .library import ArtifactCache, ProgramLibrary
from .session import (
    MemberAttached,
    MemberCommandFailed,
    MemberDetached,
    Session,
)

log = logging.getLogger(__name__)


class ControllerService:
    """Binds hub + session + library; the socket-free heart of the server.

    Construct from a Config. A hub and clock are built from the config ports
    unless injected (tests pass a fake hub and a fake clock); a library is
    built over the configured animations directory unless injected.
    """

    def __init__(self, config, *, config_path=None, hub=None, clock_us=None,
                 library=None):
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
                            clock_us=clock_us)
        self._hub = hub
        self._session = Session(hub, config.devices, clock_us)
        self._library = library if library is not None else ProgramLibrary(
            config.animations_dir or config_mod.DEFAULT_ANIMATIONS_PATH)
        self._artifact_cache = ArtifactCache()

        self._loaded_program_id = None
        self._last_state = None      # last published state dict (change gate)
        self._catalog_dirty = True   # send the catalog on the next tick
        self._shutdown = False

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
        json_msgs = [encode_json(self._event_dict(ev))
                     for ev in self._session.tick()]

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
            })
        # Devices broadcasting DISCOVER but not in the config: a UID stub the
        # operator can configure. Configured devices win, so none appears twice.
        for uid in sorted(self._hub.discovered_uids() - configured):
            devices.append({'uid': uid, 'configured': False, 'status': 'discovered'})
        return {'type': 'state', 'session': session, 'devices': devices}

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
