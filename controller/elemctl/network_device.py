"""NetworkDevice — ControllerDevice over TCP + UDP.

Sends commands to a real device (ESP32 or ESPSimulated) over a persistent TCP
connection. Receives frames via a shared UdpFrameReceiver. Tracks device state
optimistically based on commands sent.
"""

from __future__ import annotations

import logging
import select
import socket

from .device import DeviceFrame, DeviceState
from .udp_receiver import UdpFrameReceiver
from .wire import (
    ACK_ERROR,
    ACK_OK,
    ACK_WRONG_STATE,
    encode_attach,
    encode_debug_seek,
    encode_jump,
    encode_load,
    encode_pause,
    encode_reboot,
    encode_resume,
    encode_set_profile,
    encode_sync_result,
    encode_start,
    encode_stop,
    parse_ack,
)

log = logging.getLogger(__name__)

_ACK_TIMEOUT = 5.0  # seconds


class NetworkDevice:
    """ControllerDevice implementation that talks to a device over TCP + UDP."""

    def __init__(
        self,
        device_id: int,
        host: str,
        tcp_port: int,
        device_type: str,
        strip_length: int,
        frame_port: int,
        udp_receiver: UdpFrameReceiver,
    ):
        self._device_id = device_id
        self._host = host
        self._tcp_port = tcp_port
        self._device_type = device_type
        self._strip_length = strip_length
        self._frame_port = frame_port
        self._udp_receiver = udp_receiver

        self._sock: socket.socket | None = None
        self._connected = False
        self._ever_connected = False
        self._connect_error_logged = False
        self._state = DeviceState.IDLE
        self._gen = 0
        self._t0_us: int = 0
        self._last_t_rel: float = 0.0
        self._frames: list[DeviceFrame] = []
        self._last_frame_index: int | None = None
        self._last_frame_gen: int | None = None
        self._activity_observed = False

        udp_receiver.register_device(device_id)

    # ------------------------------------------------------------------
    # ControllerDevice protocol
    # ------------------------------------------------------------------

    def load(self, blob: bytes, gen: int) -> bool:
        if not self._connected and not self._connect():
            return False

        msg = encode_load(gen, blob)
        if not self._send(msg):
            return False

        status = self._recv_ack()
        if status is None:
            self._disconnect()
            return False
        if status == ACK_OK:
            self._reset_runtime_caches()
            self._state = DeviceState.LOADED
            self._gen = gen
            return True
        if status == ACK_WRONG_STATE:
            log.warning(
                'load rejected in wrong state for %s:%d (device_id=%d)',
                self._host, self._tcp_port, self._device_id,
            )
            self._disconnect()
            self._state = DeviceState.IDLE
            return False
        if status != ACK_ERROR:
            log.warning(
                'load failed with unexpected ACK status=%s for %s:%d (device_id=%d)',
                status, self._host, self._tcp_port, self._device_id,
            )

        # Device rejected the blob (decode/app failure). Per PlaybackDevice,
        # the device is now IDLE. Reset local state to match.
        self._reset_runtime_caches()
        self._state = DeviceState.IDLE
        return False

    def start(self, t0_ns: int) -> None:
        t0_us = t0_ns // 1000
        if self._send(encode_start(t0_us)):
            self._state = DeviceState.PLAYING
            self._t0_us = t0_us

    def jump(self, t0_ns: int, t_rel: float, gen: int) -> None:
        t0_us = t0_ns // 1000
        if self._send(encode_jump(t0_us, t_rel, gen)):
            was_playing = self._state == DeviceState.PLAYING
            self._gen = gen
            self._t0_us = t0_us
            if not was_playing:
                self._state = DeviceState.PAUSED
                self._last_t_rel = t_rel

    def pause(self, now_ns: int) -> None:
        if self._send(encode_pause()):
            if self._state == DeviceState.PLAYING:
                self._last_t_rel = (now_ns // 1000 - self._t0_us) / 1e6
            self._state = DeviceState.PAUSED

    def resume(self, t0_ns: int) -> None:
        t0_us = t0_ns // 1000
        if self._send(encode_resume(t0_us)):
            self._state = DeviceState.PLAYING
            self._t0_us = t0_us

    def stop(self) -> None:
        if self._send(encode_stop()):
            self._state = DeviceState.LOADED
            self._last_t_rel = 0.0

    def reboot(self) -> bool:
        if not self._connected and not self._connect():
            return False
        if not self._send(encode_reboot()):
            return False

        status = self._recv_ack()
        if status is None:
            self._disconnect()
            return False
        if status != ACK_OK:
            return False

        log.info('device %d reboot acknowledged', self._device_id)
        return True

    def send_sync_result(self, seq: int, boot_token: int, offset_us: int) -> bool:
        if not self._connected and not self._connect():
            return False
        if not self._send(encode_sync_result(seq, boot_token, offset_us)):
            return False
        log.debug(
            'device %d sync result sent seq=%d boot_token=%u offset_us=%d',
            self._device_id,
            seq,
            boot_token,
            offset_us,
        )
        return True

    def tick_once(self, now_ns: int) -> None:
        frames = self._udp_receiver.drain(self._device_id)
        self._frames.extend(frames)
        if frames:
            self._activity_observed = True
            last = frames[-1]
            self._last_frame_index = last.frame_index
            self._last_frame_gen = last.gen
            self._last_t_rel = last.t_rel
        self._check_liveness()

    def state(self) -> DeviceState:
        return self._state

    def current_t_rel(self, now_ns: int) -> float:
        if self._state == DeviceState.PLAYING:
            return (now_ns // 1000 - self._t0_us) / 1e6
        if self._state == DeviceState.PAUSED:
            return self._last_t_rel
        return 0.0

    def drain_frames(self) -> list[DeviceFrame]:
        out = self._frames[:]
        self._frames.clear()
        return out

    def produces_program_frames(self) -> bool:
        return self._device_type == 'sim'

    def supports_debug_seek(self) -> bool:
        return self._device_type == 'sim'

    def debug_seek(self, t_rel: float, now_ns: int) -> None:
        if not self.supports_debug_seek():
            return
        if self._send(encode_debug_seek(t_rel)):
            if self._state == DeviceState.PLAYING:
                self._t0_us = now_ns // 1000 - int(t_rel * 1e6)
            else:
                self._state = DeviceState.PAUSED
                self._last_t_rel = t_rel

    @property
    def is_connected(self) -> bool:
        """Whether TCP connection is currently established."""
        return self._connected

    def update_address(self, host: str, tcp_port: int) -> bool:
        """Update host/port from discovery. Disconnects if address changed.
        Returns True if the address actually changed."""
        if host == self._host and tcp_port == self._tcp_port:
            return False
        if self._connected:
            self._disconnect()
        self._host = host
        self._tcp_port = tcp_port
        self._connect_error_logged = False
        return True

    def ensure_connected(self) -> bool:
        """Attempt to establish TCP connection if not already connected.
        Returns True if connected (already or newly)."""
        if self._connected:
            return True
        if not self._has_address():
            return False
        return self._connect()

    def close(self) -> None:
        """Disconnect and reset to IDLE. Safe to call multiple times."""
        self._disconnect()
        self._state = DeviceState.IDLE

    def disconnect_transport(self) -> None:
        """Drop the live transport without resetting playback state."""
        self._disconnect()

    def consume_activity_observed(self) -> bool:
        """Return whether recent device-originated traffic was observed."""
        seen = self._activity_observed
        self._activity_observed = False
        return seen

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _has_address(self) -> bool:
        return bool(self._host) and self._tcp_port != 0

    def _connect(self) -> bool:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(_ACK_TIMEOUT)
            sock.connect((self._host, self._tcp_port))
            self._sock = sock
            self._connected = True
            if not self._send(encode_set_profile(self._strip_length)):
                return False
            status = self._recv_ack()
            if status != ACK_OK:
                log.warning(
                    'set_profile for %s:%d failed with status=%s',
                    self._host, self._tcp_port, status,
                )
                self._disconnect()
                return False
            if not self._send(encode_attach(self._device_id, self._frame_port)):
                return False
            status = self._recv_ack()
            if status != ACK_OK:
                log.warning(
                    'attach for %s:%d failed with status=%s',
                    self._host, self._tcp_port, status,
                )
                self._disconnect()
                return False
            self._ever_connected = True
            self._connect_error_logged = False
            return True
        except OSError as e:
            if not self._ever_connected and not self._connect_error_logged:
                log.warning('connect to %s:%d failed: %s', self._host, self._tcp_port, e)
                self._connect_error_logged = True
            return False

    def _disconnect(self) -> None:
        self._connected = False
        self._activity_observed = False
        self._reset_runtime_caches()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _reset_runtime_caches(self) -> None:
        """Reset session-scoped runtime observations."""
        self._t0_us = 0
        self._last_t_rel = 0.0
        self._frames.clear()
        self._last_frame_index = None
        self._last_frame_gen = None

    def _send(self, data: bytes) -> bool:
        if not self._connected or self._sock is None:
            return False
        try:
            self._sock.sendall(data)
            return True
        except OSError as e:
            log.warning('send to %s:%d failed: %s', self._host, self._tcp_port, e)
            self._disconnect()
            return False

    def _recv_exact(self, n: int) -> bytes | None:
        """Read exactly n bytes from the TCP socket. Returns None on error."""
        if not self._connected or self._sock is None:
            return None
        buf = bytearray()
        try:
            self._sock.settimeout(_ACK_TIMEOUT)
            while len(buf) < n:
                chunk = self._sock.recv(n - len(buf))
                if not chunk:
                    self._disconnect()
                    return None
                buf.extend(chunk)
                self._activity_observed = True
        except OSError as e:
            log.warning('recv from %s:%d failed: %s', self._host, self._tcp_port, e)
            self._disconnect()
            return None
        return bytes(buf)

    def _recv_ack(self) -> int | None:
        # ACK is 6 bytes: length(4) + type(1) + status(1)
        data = self._recv_exact(6)
        if data is None:
            return None
        return parse_ack(data)

    def _check_liveness(self) -> None:
        if not self._connected or self._sock is None:
            return

        try:
            readable, _, _ = select.select([self._sock], [], [], 0)
        except (OSError, ValueError) as e:
            log.warning(
                'liveness check for %s:%d failed: %s',
                self._host, self._tcp_port, e,
            )
            self._disconnect()
            return

        if not readable:
            return

        try:
            peek = self._sock.recv(1, socket.MSG_PEEK)
        except BlockingIOError:
            return
        except OSError as e:
            log.warning(
                'liveness recv from %s:%d failed: %s',
                self._host, self._tcp_port, e,
            )
            self._disconnect()
            return

        if not peek:
            self._disconnect()
