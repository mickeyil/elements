"""Shared UDP frame receiver for the controller.

The controller binds a single UDP socket on a well-known port and receives
frames from all devices. Frames are demuxed by device_id in the packet header.
"""

from __future__ import annotations

import socket

from .device import DeviceFrame
from .device_protocol import parse_udp_frame

# 10 bytes header + 3 * max_strip_length RGB.
# 4096 covers strips up to ~1362 LEDs.
_RECV_BUF_SIZE = 4096


class UdpFrameReceiver:
    """Receives UDP frames from all devices on a single shared socket."""

    def __init__(self, port: int):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(('', port))
        self._sock.setblocking(False)
        self._queues: dict[int, list[DeviceFrame]] = {}

    def register_device(self, device_id: int) -> None:
        """Register a device_id so its frames are captured."""
        self._queues.setdefault(device_id, [])

    def poll(self) -> None:
        """Non-blocking drain of the UDP socket. Parse and route by device_id."""
        while True:
            try:
                data, _addr = self._sock.recvfrom(_RECV_BUF_SIZE)
            except BlockingIOError:
                break
            parsed = parse_udp_frame(data)
            if parsed is None:
                continue
            device_id, frame = parsed
            q = self._queues.get(device_id)
            if q is not None:
                q.append(frame)

    def drain(self, device_id: int) -> list[DeviceFrame]:
        """Return and clear queued frames for a device."""
        q = self._queues.get(device_id)
        if q is None:
            return []
        frames = q[:]
        q.clear()
        return frames

    def close(self) -> None:
        self._sock.close()
