"""ControllerService — long-running service owning Controller + devices.

Testable without sockets. Owns compilation, device lifecycle, and event
conversion. The UDS server (serve.py) delegates all logic here.
"""

from __future__ import annotations

import logging
import time

from .config import Config
from .controller import (
    Controller,
    ControllerEvent,
    ControllerState,
    ProgramFrame,
    StripConfig,
)
from .network_device import NetworkDevice
from .udp_receiver import UdpFrameReceiver
from .uds_wire import encode_frame, encode_json

log = logging.getLogger(__name__)

_PROBE_INTERVAL_NS = 1_000_000_000  # 1 second


class ControllerService:
    """Core service logic — routes commands, ticks controller, converts events."""

    def __init__(
        self,
        config: Config,
        receiver_factory=UdpFrameReceiver,
        device_factory=NetworkDevice,
        clock=time.monotonic_ns,
    ):
        self._config = config
        self._clock = clock
        self._shutdown = False

        # Create receiver and devices
        self._receiver = receiver_factory(config.frame_port)
        self._devices: list = []
        self._device_configs = config.devices

        for dc in config.devices:
            dev = device_factory(
                device_id=dc.device_id,
                host=dc.host,
                tcp_port=dc.tcp_port,
                device_type=dc.device_type,
                udp_receiver=self._receiver,
            )
            self._devices.append(dev)

        # Build strip configs and controller
        self._strips = [
            StripConfig(
                strip_id=dc.strip_id,
                length=dc.length,
                device=dev,
            )
            for dc, dev in zip(config.devices, self._devices)
        ]
        self._controller = Controller(self._strips, clock=clock)

        # Probe throttle: device_id → last probe time (monotonic_ns)
        self._last_probe_ns: dict[int, int] = {}

        # Baseline connectivity for transition detection
        self._prev_connected: dict[int, bool] = {
            dc.device_id: self._is_connected(dev)
            for dc, dev in zip(config.devices, self._devices)
        }

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
            'play': self._cmd_play,
            'stop': self._cmd_stop,
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

    def _cmd_play(self, cmd: dict) -> dict:
        self._controller.play()
        return {}

    def _cmd_stop(self, cmd: dict) -> dict:
        self._controller.stop()
        return {}

    def _cmd_shutdown(self, cmd: dict) -> dict:
        self._shutdown = True
        return {}

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    def tick_once(self) -> tuple[list[bytes], list[bytes]]:
        """Poll receiver, tick controller, probe disconnected devices.

        Returns (json_messages, frame_messages) as pre-encoded UDS bytes.
        """
        self._receiver.poll()
        self._controller.tick_once()

        # Drain controller events first (before probing mutates connectivity)
        json_msgs: list[bytes] = []
        for evt in self._controller.drain_events():
            json_msgs.append(encode_json(self._event_to_dict(evt)))

        self._probe_devices(ignore_throttle=False, sync_baseline=False)

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
                    'connected': connected,
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
                'safe_intervals': [list(iv) for iv in ctrl.safe_intervals],
                'strips': [
                    {'name': sc.strip_id, 'length': sc.length}
                    for sc in self._strips
                ],
            }

        devices = []
        for dc, dev in self._iter_devices():
            connected = self._is_connected(dev)
            devices.append({
                'device_id': dc.device_id,
                'device_uid': dc.device_uid,
                'strip': dc.strip_id,
                'device_type': dc.device_type,
                'connected': connected,
            })

        return {
            'type': 'event',
            'event': 'snapshot',
            'protocol_version': 1,
            'online_count': sum(1 for d in devices if d['connected']),
            'expected_count': len(self._devices),
            'session': session,
            'devices': devices,
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

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    @staticmethod
    def _is_connected(dev) -> bool:
        return getattr(dev, 'is_connected', True)

    def _iter_devices(self):
        return zip(self._device_configs, self._devices)

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
                'strips': [
                    {'name': sc.strip_id, 'length': sc.length}
                    for sc in self._strips
                ],
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
    def _error_reply(cmd_id, message: str) -> dict:
        return {'type': 'reply', 'id': cmd_id, 'ok': False, 'error': message}
