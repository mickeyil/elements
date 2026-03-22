"""ControllerService — long-running service owning Controller + devices.

Testable without sockets. Owns compilation, device lifecycle, and event
conversion. The UDS server (server.py) delegates all logic here.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import re
import time

from elements.types import CompiledManifest, CompiledStripArtifact

from .config import (
    DEFAULT_ANIMATIONS_PATH,
    Config,
    ConfigError,
    DeviceConfig,
    MAX_DEVICE_PIXELS,
    load_config_obj,
    resolve_runtime_path,
)
from .config_edit import (
    add_device as add_device_doc,
    edit_device as edit_device_doc,
    load_config_doc,
    make_device_entry,
    next_device_id,
    remove_device as remove_device_doc,
    save_config_doc,
)
from .controller import (
    Controller,
    ControllerEvent,
    ControllerState,
    ProgramFrame,
    StripConfig,
)
from .discovery import DISCOVERY_REASON_DUPLICATE_UID, DiscoveryReceiver
from .library import ArtifactCache, ProgramEntry, ProgramLibrary
from .network_device import NetworkDevice
from .udp_receiver import UdpFrameReceiver
from .uds_wire import PROTOCOL_VERSION, encode_frame, encode_json

log = logging.getLogger(__name__)

_PROBE_INTERVAL_NS = 1_000_000_000  # 1 second
_DEVICE_UID_RE = re.compile(r'^[A-Za-z0-9._:-]+$')
_STRIP_ID_RE = re.compile(r'^[A-Za-z0-9_-]+$')
_ESP32_CANONICAL_UID_RE = re.compile(r'^esp32-([0-9a-f]{12})$')
_ESP32_FULL_HEX_RE = re.compile(r'^[0-9a-f]{12}$')
_ESP32_SHORT_HEX_RE = re.compile(r'^[0-9a-f]{6}$')


class ControllerService:
    """Core service logic — routes commands, ticks controller, converts events."""

    def __init__(
        self,
        config: Config,
        config_path: str | None = None,
        receiver_factory=UdpFrameReceiver,
        device_factory=NetworkDevice,
        discovery_factory=DiscoveryReceiver,
        library_factory=ProgramLibrary,
        clock=time.monotonic_ns,
        wall_clock=time.time,
    ):
        self._config = config
        self._config_path = config_path
        self._clock = clock
        self._wall_clock = wall_clock
        self._shutdown = False
        self._device_factory = device_factory
        self._service_events: list[dict] = []
        self._discovery_cache: dict[str, tuple[str, int]] = {}

        if config_path is not None:
            self._raw_doc = load_config_doc(config_path)
            self._config = load_config_obj(self._raw_doc)
        else:
            self._raw_doc = self._config_to_doc(config)

        # Create receiver and dynamic inventory
        self._receiver = receiver_factory(self._config.frame_port)
        self._devices: list = []
        self._device_configs: list[DeviceConfig] = []
        self._strips: list[StripConfig] = []
        self._controller: Controller | None = None

        # Discovery (optional)
        self._discovery = None
        self._uid_to_device: dict[str, tuple[DeviceConfig, object]] = {}
        if self._config.discovery_port is not None:
            self._discovery = discovery_factory(self._config.discovery_port)

        # Probe throttle: device_id → last probe time (monotonic_ns)
        self._last_probe_ns: dict[int, int] = {}

        # Baseline connectivity for transition detection
        self._prev_connected: dict[int, bool] = {}
        self._last_seen: dict[int, float | None] = {}

        animations_dir = resolve_runtime_path(
            None,
            self._config.animations_dir,
            DEFAULT_ANIMATIONS_PATH,
        )
        self._library = library_factory(animations_dir)
        self._artifact_cache = ArtifactCache()

        self._reconcile_devices(self._config)
        self._rebuild_controller()

    # ------------------------------------------------------------------
    # Command handling
    # ------------------------------------------------------------------

    def handle_cmd(self, cmd: dict) -> dict:
        """Route a command dict, return a reply dict."""
        cmd_id = cmd.get('id')
        action = cmd.get('cmd')

        if action is None:
            return self._error_reply(cmd_id, "missing 'cmd' field")

        handler = {
            'status': self._cmd_status,
            'load': self._cmd_load,
            'load_program': self._cmd_load_program,
            'load_scene': self._cmd_load_scene,
            'play': self._cmd_play,
            'pause': self._cmd_pause,
            'publish_program': self._cmd_publish_program,
            'rescan_programs': self._cmd_rescan_programs,
            'seek': self._cmd_seek,
            'debug_seek': self._cmd_debug_seek,
            'stop': self._cmd_stop,
            'add_device': self._cmd_add_device,
            'edit_device': self._cmd_edit_device,
            'remove_device': self._cmd_remove_device,
            'shutdown': self._cmd_shutdown,
        }.get(action)

        if handler is None:
            return self._error_reply(cmd_id, f"unknown command: {action!r}")

        try:
            result = handler(cmd)
            return {'type': 'reply', 'id': cmd_id, 'ok': True, 'result': result}
        except Exception as e:
            return self._error_reply(cmd_id, str(e))

    def _cmd_status(self, cmd: dict) -> dict:
        return self.build_snapshot()

    def _cmd_load(self, cmd: dict) -> dict:
        self._require_configured_devices_for_load()
        self._require_unique_strip_id_topology_for_legacy_load()
        source = cmd.get('source')
        beat = cmd.get('beat')
        duration = cmd.get('duration')
        loop = cmd.get('loop', False)

        if source is None:
            raise ValueError("missing 'source' field")
        if beat is None:
            raise ValueError("missing 'beat' field")
        if duration is None:
            raise ValueError("missing 'duration' field")

        manifest = self._compile(source, beat, duration)

        if not self._controller.load(manifest, loop=loop):
            errors = self._controller.drain_events()
            msg = '; '.join(e.message for e in errors if e.message)
            raise ValueError(f"load failed: {msg}" if msg else "load failed")

        return {'session_id': self._controller.session_id}

    def _cmd_load_program(self, cmd: dict) -> dict:
        self._require_configured_devices_for_load()
        program_id = cmd.get('program_id')
        targets = self._require_targets(cmd.get('targets'))
        loop = cmd.get('loop', False)
        _entry, manifest, target_groups = self._prepare_program_load(program_id, targets)

        if not self._controller.load(manifest, loop=loop, target_groups=target_groups):
            errors = self._controller.drain_events()
            msg = '; '.join(e.message for e in errors if e.message)
            raise ValueError(f"load failed: {msg}" if msg else "load failed")

        return {'session_id': self._controller.session_id}

    def _cmd_load_scene(self, cmd: dict) -> dict:
        plan = self._prepare_scene_plan(cmd)
        entries = plan['entries']
        if not entries:
            raise ValueError('scene must contain at least one entry')

        duration = entries[0]['duration']
        combined_strips: list[CompiledStripArtifact] = []
        combined_target_groups: list[list[int]] = []
        merged_safe_intervals = list(entries[0]['safe_intervals'])

        for index, entry in enumerate(entries):
            entry_duration = entry['duration']
            if not math.isclose(duration, entry_duration, rel_tol=0.0, abs_tol=1e-9):
                raise ValueError(
                    'scene entries must share one duration '
                    f'(entry {index + 1} {entry["program_id"]}: {entry_duration:g}s '
                    f'!= {duration:g}s)'
                )
            manifest = entry['manifest']
            combined_strips.extend(manifest.strips)
            combined_target_groups.extend(entry['target_groups'])
            if index > 0:
                merged_safe_intervals = self._intersect_safe_intervals(
                    merged_safe_intervals,
                    entry['safe_intervals'],
                )

        manifest = CompiledManifest(
            duration=duration,
            strips=[
                CompiledStripArtifact(
                    strip_id=strip.strip_id,
                    length=strip.length,
                    blob=strip.blob,
                )
                for strip in combined_strips
            ],
            safe_intervals=merged_safe_intervals,
        )
        if not self._controller.load(
            manifest,
            loop=plan['loop'],
            target_groups=combined_target_groups,
        ):
            errors = self._controller.drain_events()
            msg = '; '.join(e.message for e in errors if e.message)
            raise ValueError(f"load failed: {msg}" if msg else "load failed")

        return {'session_id': self._controller.session_id}

    def _cmd_publish_program(self, cmd: dict) -> dict:
        program_id = cmd.get('program_id')
        source = cmd.get('source')
        if not isinstance(program_id, str) or not program_id:
            raise ValueError("missing 'program_id' field")
        if source is None:
            raise ValueError("missing 'source' field")
        if not isinstance(source, str):
            raise ValueError("'source' must be a string")

        entry = self._library.publish(program_id, source)
        programs = self._programs_to_wire()
        self._service_events.append({
            'type': 'event',
            'event': 'programs_updated',
            'programs': programs,
        })
        return {'program': self._program_to_dict(entry)}

    def _cmd_play(self, cmd: dict) -> dict:
        self._controller.play()
        return {}

    def _cmd_pause(self, cmd: dict) -> dict:
        self._controller.pause()
        return {}

    def _cmd_seek(self, cmd: dict) -> dict:
        self._controller.seek(self._require_t_rel(cmd))
        return {}

    def _cmd_debug_seek(self, cmd: dict) -> dict:
        self._controller.debug_seek(self._require_t_rel(cmd))
        return {}

    def _cmd_stop(self, cmd: dict) -> dict:
        self._controller.stop()
        return {}

    def _cmd_shutdown(self, cmd: dict) -> dict:
        self._shutdown = True
        return {}

    def _cmd_rescan_programs(self, cmd: dict) -> dict:
        self._library.rescan()
        programs = self._programs_to_wire()
        self._service_events.append({
            'type': 'event',
            'event': 'programs_updated',
            'programs': programs,
        })
        return {'programs': programs}

    def _cmd_add_device(self, cmd: dict) -> dict:
        self._require_mutation_quiescent()
        candidate = copy.deepcopy(self._raw_doc)

        device_type = cmd.get('device_type')
        device_uid = cmd.get('device_uid')
        strip_id = cmd.get('strip_id')
        length = self._require_length(cmd.get('length'))

        self._validate_device_type(device_type)
        self._validate_device_uid(device_type, device_uid)
        device_uid = self._normalize_device_uid(device_type, device_uid)
        self._validate_strip_id(strip_id)
        self._ensure_device_uid_available(device_type, device_uid)

        entry = make_device_entry(
            device_uid=device_uid,
            device_type=device_type,
            strip_id=strip_id,
            length=length,
            device_id=next_device_id(candidate),
        )
        add_device_doc(candidate, entry)
        new_config = self._save_and_validate_candidate(candidate)
        self._raw_doc = candidate
        self._reconcile_devices(new_config)
        self._rebuild_controller()
        self._queue_snapshot_event()
        return {'message': f'added device {device_uid}'}

    def _cmd_edit_device(self, cmd: dict) -> dict:
        self._require_mutation_quiescent()
        target_device_uid = cmd.get('target_device_uid')
        device_uid = cmd.get('device_uid')
        strip_id = cmd.get('strip_id')
        length = self._require_length(cmd.get('length'))

        if not isinstance(target_device_uid, str) or not target_device_uid:
            raise ValueError("missing 'target_device_uid' field")
        target_dc = next(
            (dc for dc in self._device_configs if dc.device_uid == target_device_uid),
            None,
        )
        if target_dc is None:
            raise ValueError(f'device not found: {target_device_uid}')

        self._validate_device_uid(target_dc.device_type, device_uid)
        device_uid = self._normalize_device_uid(target_dc.device_type, device_uid)
        self._validate_strip_id(strip_id)
        self._ensure_device_uid_available(
            target_dc.device_type,
            device_uid,
            skip_uid=target_device_uid,
        )

        candidate = copy.deepcopy(self._raw_doc)
        edit_device_doc(
            candidate,
            target_device_uid,
            device_uid=device_uid,
            strip_id=strip_id,
            length=length,
        )
        new_config = self._save_and_validate_candidate(candidate)
        self._raw_doc = candidate
        self._reconcile_devices(new_config)
        self._rebuild_controller()
        self._queue_snapshot_event()
        if target_device_uid == device_uid:
            return {'message': f'updated device {device_uid}'}
        return {'message': f'updated device {target_device_uid} -> {device_uid}'}

    def _cmd_remove_device(self, cmd: dict) -> dict:
        self._require_mutation_quiescent()
        device_uid = cmd.get('device_uid')
        if not isinstance(device_uid, str) or not device_uid:
            raise ValueError("missing 'device_uid' field")

        candidate = copy.deepcopy(self._raw_doc)
        remove_device_doc(candidate, device_uid)
        new_config = self._save_and_validate_candidate(candidate)
        self._raw_doc = candidate
        self._reconcile_devices(new_config)
        self._rebuild_controller()
        self._queue_snapshot_event()
        return {'message': f'removed device {device_uid}'}

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    def tick_once(self) -> tuple[list[bytes], list[bytes]]:
        """Poll receiver, tick controller, probe disconnected devices.

        Returns (json_messages, frame_messages) as pre-encoded UDS bytes.
        """
        self._receiver.poll()
        self._poll_discovery()
        if self._controller.state == ControllerState.IDLE:
            now = self._clock()
            for dev in self._devices:
                dev.tick_once(now)
        self._controller.tick_once()

        # Drain controller events first (before probing mutates connectivity)
        json_msgs: list[bytes] = []
        while self._service_events:
            json_msgs.append(encode_json(self._service_events.pop(0)))
        for evt in self._controller.drain_events():
            json_msgs.append(encode_json(self._event_to_dict(evt)))

        self._probe_devices(ignore_throttle=False, sync_baseline=False)

        now_wall = self._wall_clock()
        for dc, dev in self._iter_devices():
            if self._is_connected(dev):
                self._last_seen[dc.device_id] = now_wall

        # Detect connectivity transitions
        for dc, dev in self._iter_devices():
            connected = self._is_connected(dev)
            if connected != self._prev_connected[dc.device_id]:
                self._prev_connected[dc.device_id] = connected
                json_msgs.append(encode_json({
                    'type': 'event',
                    'event': 'device_status',
                    'device_id': dc.device_id,
                    'device_uid': dc.device_uid,
                    'strip': dc.strip_id,
                    'length': dc.length,
                    'connected': connected,
                    'last_seen': self._last_seen.get(dc.device_id),
                }))

        # Convert program frames
        frame_msgs: list[bytes] = []
        for pf in self._controller.drain_program_frames():
            frame_msgs.append(encode_frame(pf.frame_index, pf.t_rel, pf.strips))

        return json_msgs, frame_msgs

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def build_snapshot(self) -> dict:
        """Build current-state snapshot dict."""
        ctrl = self._controller

        session = None
        if ctrl.state != ControllerState.IDLE:
            session = {
                'session_id': ctrl.session_id,
                'epoch': ctrl.epoch,
                'playback_state': ctrl.state.name.lower(),
                'duration': ctrl.duration,
                'current_t_rel': ctrl.current_t_rel,
                'safe_intervals': [list(iv) for iv in ctrl.safe_intervals],
                'strips': self._session_strips_to_wire(ctrl),
            }

        devices = []
        for dc, dev in self._iter_devices():
            connected = self._is_connected(dev)
            devices.append({
                'device_id': dc.device_id,
                'device_uid': dc.device_uid,
                'strip': dc.strip_id,
                'length': dc.length,
                'device_type': dc.device_type,
                'connected': connected,
                'last_seen': self._last_seen.get(dc.device_id),
            })

        return {
            'type': 'event',
            'event': 'snapshot',
            'protocol_version': PROTOCOL_VERSION,
            'online_count': sum(1 for d in devices if d['connected']),
            'expected_count': len(self._devices),
            'session': session,
            'devices': devices,
            'programs': self._programs_to_wire(),
        }

    def probe_all(self) -> None:
        """Probe all disconnected devices immediately (ignores throttle)."""
        self._probe_devices(ignore_throttle=True, sync_baseline=True)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def should_shutdown(self) -> bool:
        return self._shutdown

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close all devices and receiver. Idempotent."""
        for dev in self._devices:
            dev.close()
        self._receiver.close()
        if self._discovery is not None:
            self._discovery.close()
        self._discovery_cache.clear()

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    @staticmethod
    def _is_connected(dev) -> bool:
        return getattr(dev, 'is_connected', True)

    def _iter_devices(self):
        return zip(self._device_configs, self._devices)

    @staticmethod
    def _normalize_device_uid(device_type: str, device_uid: str) -> str:
        if device_type == 'esp32':
            parsed = ControllerService._parse_esp32_uid(device_uid)
            if parsed is None:
                return device_uid.lower()
            if parsed['kind'] == 'full':
                return f"esp32-{parsed['full_hex']}"
            return parsed['suffix']
        return device_uid

    @staticmethod
    def _parse_esp32_uid(device_uid: str) -> dict | None:
        if not isinstance(device_uid, str):
            return None
        normalized = device_uid.lower()
        match = _ESP32_CANONICAL_UID_RE.fullmatch(normalized)
        if match is not None:
            full_hex = match.group(1)
            return {
                'kind': 'full',
                'full_hex': full_hex,
                'suffix': full_hex[-6:],
                'canonical': True,
            }
        if _ESP32_FULL_HEX_RE.fullmatch(normalized):
            return {
                'kind': 'full',
                'full_hex': normalized,
                'suffix': normalized[-6:],
                'canonical': False,
            }
        if _ESP32_SHORT_HEX_RE.fullmatch(normalized):
            return {
                'kind': 'short',
                'full_hex': None,
                'suffix': normalized,
                'canonical': False,
            }
        return None

    @classmethod
    def _esp32_uids_conflict(cls, existing_uid: str, proposed_uid: str) -> bool:
        existing = cls._parse_esp32_uid(existing_uid)
        proposed = cls._parse_esp32_uid(proposed_uid)
        if existing is None or proposed is None:
            return existing_uid == proposed_uid
        if existing['kind'] == 'full' and proposed['kind'] == 'full':
            return existing['full_hex'] == proposed['full_hex']
        return existing['suffix'] == proposed['suffix']

    @classmethod
    def _esp32_uid_matches_discovered(
        cls,
        configured_uid: str,
        discovered_uid: str,
    ) -> bool:
        configured = cls._parse_esp32_uid(configured_uid)
        discovered = cls._parse_esp32_uid(discovered_uid)
        if configured is None or discovered is None or discovered['kind'] != 'full':
            return False
        if configured['kind'] == 'full':
            return configured['full_hex'] == discovered['full_hex']
        return configured['suffix'] == discovered['suffix']

    def _ensure_device_uid_available(
        self,
        device_type: str,
        device_uid: str,
        *,
        skip_uid: str | None = None,
    ) -> None:
        for dc in self._device_configs:
            if skip_uid is not None and dc.device_uid == skip_uid:
                continue
            if device_type == 'esp32' and dc.device_type == 'esp32':
                if self._esp32_uids_conflict(dc.device_uid, device_uid):
                    raise ValueError(f'device uid already exists: {device_uid}')
                continue
            if dc.device_uid == device_uid:
                raise ValueError(f'device uid already exists: {device_uid}')

    def _find_esp32_promotion_candidates(self, discovered_uid: str) -> list[DeviceConfig]:
        discovered = self._parse_esp32_uid(discovered_uid)
        if (
            discovered is None
            or discovered['kind'] != 'full'
            or not discovered['canonical']
        ):
            return []
        candidates: list[DeviceConfig] = []
        for dc in self._device_configs:
            if dc.device_type != 'esp32':
                continue
            if self._esp32_uid_matches_discovered(dc.device_uid, discovered_uid):
                candidates.append(dc)
        return candidates

    def _promote_configured_esp32_uid(
        self,
        target_dc: DeviceConfig,
        canonical_uid: str,
    ) -> bool:
        canonical_uid = canonical_uid.lower()
        if target_dc.device_uid == canonical_uid:
            return True
        if self._config_path is None:
            raise ValueError('config path unavailable')
        self._ensure_device_uid_available(
            'esp32',
            canonical_uid,
            skip_uid=target_dc.device_uid,
        )
        candidate = copy.deepcopy(self._raw_doc)
        edit_device_doc(
            candidate,
            target_dc.device_uid,
            device_uid=canonical_uid,
            strip_id=target_dc.strip_id,
            length=target_dc.length,
        )
        new_config = self._save_and_validate_candidate(candidate)
        self._raw_doc = candidate
        self._reconcile_devices(new_config)
        self._rebuild_controller()
        self._queue_snapshot_event()
        log.info(
            'discovery: promoted esp32 uid %s -> %s',
            target_dc.device_uid,
            canonical_uid,
        )
        return True

    def _maybe_promote_discovered_esp32_uid(self, discovered_uid: str) -> bool:
        candidates = self._find_esp32_promotion_candidates(discovered_uid)
        if not candidates:
            return False
        if len(candidates) != 1:
            log.error(
                'discovery: ambiguous provisional esp32 uid for %s: %s',
                discovered_uid,
                ', '.join(sorted(dc.device_uid for dc in candidates)),
            )
            return False
        target_dc = candidates[0]
        try:
            return self._promote_configured_esp32_uid(target_dc, discovered_uid)
        except ValueError as e:
            log.error(
                'discovery: failed to promote esp32 uid %s -> %s: %s',
                target_dc.device_uid,
                discovered_uid,
                e,
            )
            return False

    def _poll_discovery(self) -> None:
        if self._discovery is None:
            return
        self._discovery.poll()
        for uid, host, tcp_port, reply_port in self._discovery.drain_discoveries():
            entry = self._uid_to_device.get(uid)
            if entry is None:
                self._discovery_cache[uid] = (host, tcp_port)
                if self._maybe_promote_discovered_esp32_uid(uid):
                    entry = self._uid_to_device.get(uid)
            if entry is None:
                log.debug('discovery: unknown uid %r from %s:%d', uid, host, tcp_port)
                continue
            dc, dev = entry
            current_host = getattr(dev, '_host', None)
            current_port = getattr(dev, '_tcp_port', None)
            current_endpoint_known = bool(current_host) and current_port not in (None, 0)
            if (
                self._is_connected(dev)
                and current_endpoint_known
                and (host, tcp_port) != (current_host, current_port)
            ):
                self._discovery.send_reject(
                    host, reply_port, DISCOVERY_REASON_DUPLICATE_UID
                )
                log.debug(
                    'discovery: ignoring duplicate live uid %r at %s:%d',
                    uid, host, tcp_port,
                )
                continue
            self._discovery_cache[uid] = (host, tcp_port)
            changed = dev.update_address(host, tcp_port)
            if changed:
                log.info(
                    'discovery: connected to %s at %s:%d (strip %s, %d LEDs)',
                    dc.device_uid, host, tcp_port, dc.strip_id, dc.length,
                )
                # Clear probe throttle so _probe_devices() connects immediately
                self._last_probe_ns.pop(dc.device_id, None)

    def _probe_devices(self, *, ignore_throttle: bool, sync_baseline: bool) -> None:
        now = self._clock()
        for dc, dev in self._iter_devices():
            if self._is_connected(dev):
                continue
            if not ignore_throttle:
                last = self._last_probe_ns.get(dc.device_id, 0)
                if now - last < _PROBE_INTERVAL_NS:
                    continue
                self._last_probe_ns[dc.device_id] = now
            dev.ensure_connected()
            if sync_baseline:
                self._prev_connected[dc.device_id] = self._is_connected(dev)

    def _compile(self, source: str, beat: float, duration: float):
        from elements.dsl import _builder, build_manifest
        _builder.reset()
        _builder.configured_strip_lengths = self._logical_strip_lengths()
        try:
            exec(source, {'__builtins__': __builtins__})
            return build_manifest(beat=beat, duration=duration)
        finally:
            _builder.reset()

    def _event_to_dict(self, evt: ControllerEvent) -> dict:
        kind = evt.kind
        if kind == ControllerEvent.Kind.SESSION_STARTED:
            ctrl = self._controller
            return {
                'type': 'event',
                'event': 'session_start',
                'session_id': evt.session_id,
                'epoch': evt.epoch,
                'duration': ctrl.duration,
                'safe_intervals': [list(iv) for iv in ctrl.safe_intervals],
                'strips': self._session_strips_to_wire(ctrl),
            }
        if kind == ControllerEvent.Kind.STATE_CHANGED:
            return {
                'type': 'event',
                'event': 'state',
                'state': evt.state.name.lower(),
                'epoch': evt.epoch,
                'session_id': evt.session_id,
            }
        if kind == ControllerEvent.Kind.LOOPED:
            return {
                'type': 'event',
                'event': 'loop',
                'epoch': evt.epoch,
                'session_id': evt.session_id,
            }
        if kind == ControllerEvent.Kind.ERROR:
            return {
                'type': 'event',
                'event': 'error',
                'message': evt.message,
            }
        return {'type': 'event', 'event': 'unknown'}

    @staticmethod
    def _require_t_rel(cmd: dict) -> float:
        t_rel = cmd.get('t_rel')
        if t_rel is None:
            raise ValueError("missing 't_rel' field")
        if isinstance(t_rel, bool):
            raise ValueError("'t_rel' must be a finite number")
        value = float(t_rel)
        if not math.isfinite(value):
            raise ValueError("'t_rel' must be a finite number")
        return value

    @staticmethod
    def _error_reply(cmd_id, message: str) -> dict:
        return {'type': 'reply', 'id': cmd_id, 'ok': False, 'error': message}

    @staticmethod
    def _config_to_doc(config: Config) -> dict:
        controller = {
            'frame_port': config.frame_port,
            'discovery_port': config.discovery_port,
        }
        if config.animations_dir is not None:
            controller['animations_dir'] = config.animations_dir
        if config.logs_dir is not None:
            controller['logs_dir'] = config.logs_dir
        return {
            'controller': controller,
            'devices': [
                {
                    'device_id': dc.device_id,
                    'device_uid': dc.device_uid,
                    'device_type': dc.device_type,
                    'host': dc.host,
                    'tcp_port': dc.tcp_port,
                    'strip_id': dc.strip_id,
                    'length': dc.length,
                }
                for dc in config.devices
            ],
        }

    def _make_device(self, dc: DeviceConfig):
        dev = self._device_factory(
            device_id=dc.device_id,
            host=dc.host,
            tcp_port=dc.tcp_port,
            device_type=dc.device_type,
            strip_length=dc.length,
            frame_port=self._config.frame_port,
            udp_receiver=self._receiver,
        )
        cached = self._discovery_cache.get(dc.device_uid)
        if cached is not None:
            host, tcp_port = cached
            try:
                changed = dev.update_address(host, tcp_port)
            except AttributeError:
                changed = False
            if changed:
                self._last_probe_ns.pop(dc.device_id, None)
        return dev

    def _reconcile_devices(self, new_config: Config) -> None:
        current_by_uid = {
            dc.device_uid: (dc, dev)
            for dc, dev in zip(self._device_configs, self._devices)
        }
        new_devices: list[object] = []
        new_prev_connected: dict[int, bool] = {}
        new_last_probe_ns: dict[int, int] = {}
        new_last_seen: dict[int, float | None] = {}
        now_wall = self._wall_clock()

        for dc in new_config.devices:
            existing = current_by_uid.pop(dc.device_uid, None)
            if existing is not None and existing[0] == dc:
                old_dc, dev = existing
                new_devices.append(dev)
                new_prev_connected[dc.device_id] = self._is_connected(dev)
                if old_dc.device_id in self._last_probe_ns:
                    new_last_probe_ns[dc.device_id] = self._last_probe_ns[old_dc.device_id]
                preserved_last_seen = self._last_seen.get(old_dc.device_id)
                if preserved_last_seen is None and self._is_connected(dev):
                    preserved_last_seen = now_wall
                new_last_seen[dc.device_id] = preserved_last_seen
                continue

            if existing is not None:
                _old_dc, old_dev = existing
                old_dev.close()

            dev = self._make_device(dc)
            new_devices.append(dev)
            new_prev_connected[dc.device_id] = self._is_connected(dev)
            new_last_seen[dc.device_id] = now_wall if self._is_connected(dev) else None

        for _old_dc, dev in current_by_uid.values():
            dev.close()

        self._config = new_config
        self._device_configs = list(new_config.devices)
        self._devices = new_devices
        self._prev_connected = new_prev_connected
        self._last_probe_ns = new_last_probe_ns
        self._last_seen = new_last_seen
        if self._discovery is not None:
            self._uid_to_device = {
                dc.device_uid: (dc, dev)
                for dc, dev in zip(self._device_configs, self._devices)
            }
        else:
            self._uid_to_device = {}

    def _rebuild_controller(self) -> None:
        self._strips = [
            StripConfig(strip_id=dc.strip_id, length=dc.length, device=dev)
            for dc, dev in zip(self._device_configs, self._devices)
        ]
        self._controller = Controller(self._strips, clock=self._clock)

    def _queue_snapshot_event(self) -> None:
        self._service_events.append(self.build_snapshot())

    @staticmethod
    def _program_to_dict(entry: ProgramEntry) -> dict:
        result = {
            'program_id': entry.program_id,
            'beat': entry.beat,
            'duration': entry.duration,
            'error': entry.error,
        }
        if entry.strips is not None:
            result['strips'] = list(entry.strips)
        return result

    def _programs_to_wire(self) -> list[dict]:
        return [self._program_to_dict(entry) for entry in self._library.list_programs()]

    def _logical_strip_lengths(self) -> dict[str, int]:
        lengths: dict[str, int] = {}
        for dc in self._device_configs:
            existing = lengths.get(dc.strip_id)
            if existing is None:
                lengths[dc.strip_id] = dc.length
            elif existing != dc.length:
                raise RuntimeError(
                    'strip_id length conflict should have been caught by config validation'
                )
        return lengths

    def _topology_fingerprint(self) -> str:
        parts = sorted(self._logical_strip_lengths().items())
        return hashlib.sha256(
            json.dumps(parts, separators=(',', ':')).encode('utf-8')
        ).hexdigest()

    def _require_mutation_quiescent(self) -> None:
        allowed = {
            ControllerState.IDLE,
            ControllerState.STOPPED,
            ControllerState.ENDED,
        }
        if self._controller.state not in allowed:
            state = self._controller.state.name.lower()
            raise ValueError(
                f'controller must be idle or stopped before changing devices (current state: {state})'
            )
        if self._config_path is None:
            raise ValueError('config path unavailable')

    def _require_configured_devices_for_load(self) -> None:
        if not self._device_configs:
            raise ValueError('no configured devices')

    def _require_unique_strip_id_topology_for_legacy_load(self) -> None:
        strip_ids = {dc.strip_id for dc in self._device_configs}
        if len(strip_ids) != len(self._device_configs):
            raise ValueError('duplicate strip_id topology requires targeted load support')

    def _require_targets(self, targets) -> list[str] | None:
        if targets is None:
            return None
        if not isinstance(targets, list):
            raise ValueError("'targets' must be a list of device_uids")
        out: list[str] = []
        seen: set[str] = set()
        for target in targets:
            if not isinstance(target, str) or not target:
                raise ValueError("'targets' must be a list of device_uids")
            if target in seen:
                raise ValueError(f'duplicate target: {target}')
            seen.add(target)
            out.append(target)
        return out

    def _prepare_program_load(
        self,
        program_id,
        targets: list[str] | None,
    ) -> tuple[ProgramEntry, object, list[list[int]]]:
        if not isinstance(program_id, str) or not program_id:
            raise ValueError("missing 'program_id' field")

        entry = self._library.get(program_id)
        if entry is None:
            raise ValueError(f'program not found: {program_id}')
        if entry.error is not None:
            raise ValueError(f'program {program_id} is not loadable: {entry.error}')
        if entry.source is None or entry.source_hash is None:
            raise ValueError(f'program {program_id} is missing source')
        if entry.beat is None or entry.duration is None:
            raise ValueError(f'program {program_id} is missing metadata')

        topology = self._topology_fingerprint()
        manifest = self._artifact_cache.get(entry.source_hash, topology)
        if manifest is None:
            manifest = self._compile(entry.source, entry.beat, entry.duration)
            self._artifact_cache.put(entry.source_hash, topology, manifest)

        target_groups = self._resolve_manifest_target_groups(manifest, targets)
        return entry, manifest, target_groups

    def _prepare_scene_plan(self, cmd: dict) -> dict:
        self._require_configured_devices_for_load()

        loop = cmd.get('loop', False)
        if not isinstance(loop, bool):
            raise ValueError("'loop' must be a boolean")

        entries = cmd.get('entries')
        if entries is None:
            raise ValueError("missing 'entries' field")
        if not isinstance(entries, list) or not entries:
            raise ValueError("'entries' must be a non-empty list")

        prepared_entries: list[dict] = []
        seen_targets: dict[str, tuple[int, str]] = {}

        for entry_index, entry_cmd in enumerate(entries, start=1):
            if not isinstance(entry_cmd, dict):
                raise ValueError(f'entry {entry_index}: must be an object')

            program_id = entry_cmd.get('program_id')
            if not isinstance(program_id, str) or not program_id:
                raise ValueError(f"entry {entry_index}: missing 'program_id' field")

            if 'targets' not in entry_cmd:
                raise ValueError(f"entry {entry_index} ({program_id}): missing 'targets' field")
            try:
                targets = self._require_targets(entry_cmd.get('targets'))
            except ValueError as e:
                raise ValueError(f'entry {entry_index} ({program_id}): {e}') from e
            if not targets:
                raise ValueError(f"entry {entry_index} ({program_id}): 'targets' must be a non-empty list")

            for target in targets:
                previous = seen_targets.get(target)
                if previous is not None:
                    prev_index, prev_program_id = previous
                    raise ValueError(
                        f'entry {entry_index} ({program_id}): duplicate target across scene: '
                        f'{target} already used by entry {prev_index} ({prev_program_id})'
                    )

            try:
                _entry, manifest, target_groups = self._prepare_program_load(program_id, targets)
            except ValueError as e:
                raise ValueError(f'entry {entry_index} ({program_id}): {e}') from e

            for target in targets:
                seen_targets[target] = (entry_index, program_id)

            prepared_entries.append({
                'program_id': program_id,
                'targets': list(targets),
                'manifest': manifest,
                'target_groups': [list(group) for group in target_groups],
                'duration': manifest.duration,
                'safe_intervals': list(manifest.safe_intervals),
            })

        return {
            'loop': loop,
            'entries': prepared_entries,
        }

    @staticmethod
    def _intersect_safe_intervals(
        left: list[tuple[float, float]],
        right: list[tuple[float, float]],
    ) -> list[tuple[float, float]]:
        if not left or not right:
            return []
        i = 0
        j = 0
        out: list[tuple[float, float]] = []
        while i < len(left) and j < len(right):
            lo = max(left[i][0], right[j][0])
            hi = min(left[i][1], right[j][1])
            if lo <= hi:
                out.append((lo, hi))
            if left[i][1] < right[j][1]:
                i += 1
            else:
                j += 1
        return out

    def _session_strips_to_wire(self, ctrl: Controller) -> list[dict]:
        strips = ctrl.session_strips
        target_groups = ctrl.session_target_groups
        if len(strips) != len(target_groups):
            raise RuntimeError(
                'session strip metadata mismatch between logical strips and target groups'
            )

        wire: list[dict] = []
        for (name, length), group in zip(strips, target_groups):
            targets: list[dict] = []
            for index in group:
                if not (0 <= index < len(self._device_configs)):
                    raise RuntimeError(f'session target index out of range: {index}')
                dc = self._device_configs[index]
                targets.append({
                    'device_id': dc.device_id,
                    'device_uid': dc.device_uid,
                    'device_type': dc.device_type,
                    'length': dc.length,
                })
            wire.append({
                'name': name,
                'length': length,
                'targets': targets,
            })
        return wire

    def _resolve_manifest_target_groups(
        self,
        manifest,
        targets: list[str] | None,
    ) -> list[list[int]]:
        manifest_strip_ids = [ms.strip_id for ms in manifest.strips]
        if len(set(manifest_strip_ids)) != len(manifest_strip_ids):
            raise ValueError('duplicate program strip ids are not supported')
        manifest_strip_id_set = set(manifest_strip_ids)

        if targets is None:
            selected_indices = [
                i for i, dc in enumerate(self._device_configs)
                if dc.strip_id in manifest_strip_id_set
            ]
        else:
            index_by_uid = {
                dc.device_uid: i for i, dc in enumerate(self._device_configs)
            }
            selected_indices = []
            for target in targets:
                index = index_by_uid.get(target)
                if index is None:
                    raise ValueError(f'target not found: {target}')
                selected_indices.append(index)

        selected_by_strip: dict[str, list[int]] = {}
        for index in selected_indices:
            dc = self._device_configs[index]
            if dc.strip_id not in manifest_strip_id_set:
                raise ValueError(f'unused target: {dc.device_uid}')
            selected_by_strip.setdefault(dc.strip_id, []).append(index)

        target_groups: list[list[int]] = []
        for ms in manifest.strips:
            group = list(selected_by_strip.get(ms.strip_id, []))
            if not group:
                raise ValueError(f'missing targets for strip: {ms.strip_id}')
            for index in group:
                dc = self._device_configs[index]
                if ms.length > dc.length:
                    raise ValueError(
                        f'strip length exceeds configured length for '
                        f'{dc.strip_id} ({dc.device_uid})'
                    )
            target_groups.append(group)

        return target_groups

    def _save_and_validate_candidate(self, candidate: dict) -> Config:
        try:
            new_config = load_config_obj(candidate)
        except ConfigError as e:
            raise ValueError(str(e))
        try:
            save_config_doc(self._config_path, candidate)
        except OSError as e:
            raise ValueError(f'cannot save config: {e}')
        return new_config

    @staticmethod
    def _validate_device_type(device_type) -> None:
        if device_type not in {'sim', 'esp32'}:
            raise ValueError("device_type must be 'sim' or 'esp32'")

    @staticmethod
    def _validate_device_uid(device_type, device_uid) -> None:
        if not isinstance(device_uid, str) or not device_uid:
            raise ValueError("device uid is required")
        if not _DEVICE_UID_RE.fullmatch(device_uid):
            raise ValueError("device uid may only contain letters, numbers, ., _, -, and :")
        if device_type == 'esp32':
            if ControllerService._parse_esp32_uid(device_uid) is None:
                raise ValueError(
                    "esp32 device uid must be 6 hex, 12 hex, or esp32-<12 hex>"
                )

    @staticmethod
    def _validate_strip_id(strip_id) -> None:
        if not isinstance(strip_id, str) or not strip_id:
            raise ValueError("strip id is required")
        if not _STRIP_ID_RE.fullmatch(strip_id):
            raise ValueError("strip id may only contain letters, numbers, _ and -")

    @staticmethod
    def _require_length(value) -> int:
        if isinstance(value, bool) or value is None:
            raise ValueError("length is required")
        try:
            length = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"length must be an integer, got {value!r}")
        if not (1 <= length <= MAX_DEVICE_PIXELS):
            raise ValueError(f"length must be 1-{MAX_DEVICE_PIXELS}, got {length}")
        return length
