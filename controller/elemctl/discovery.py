"""Device discovery via UDP HELLO broadcast.

Devices periodically send a HELLO packet to a well-known port. The controller
listens and learns (device_uid, host, tcp_port) tuples. This is a separate
protocol from the TCP command wire format.

HELLO packet layout (UDP, no length-prefix):
  Offset  Size  Field
  0       2     magic: 0x454C ("EL")
  2       2     tcp_port (u16 LE)
  4       1     uid_len (u8)
  5       N     device_uid (UTF-8)
"""

from __future__ import annotations

import socket
import struct

DISCOVERY_MAGIC = 0x454C


def encode_hello(device_uid: str, tcp_port: int) -> bytes:
    """Encode a HELLO packet for broadcast."""
    if not device_uid:
        raise ValueError('device_uid must be non-empty')
    uid_bytes = device_uid.encode('utf-8')
    if len(uid_bytes) > 255:
        raise ValueError(f'device_uid too long ({len(uid_bytes)} bytes, max 255)')
    if tcp_port == 0:
        raise ValueError('tcp_port must be non-zero')
    return struct.pack('<HHB', DISCOVERY_MAGIC, tcp_port, len(uid_bytes)) + uid_bytes


def parse_hello(data: bytes) -> tuple[str, int] | None:
    """Parse a HELLO packet. Returns (device_uid, tcp_port) or None."""
    if len(data) < 5:
        return None
    magic, tcp_port, uid_len = struct.unpack_from('<HHB', data, 0)
    if magic != DISCOVERY_MAGIC or len(data) < 5 + uid_len:
        return None
    if tcp_port == 0 or uid_len == 0:
        return None
    try:
        device_uid = data[5:5 + uid_len].decode('utf-8')
    except UnicodeDecodeError:
        return None
    return device_uid, tcp_port


class DiscoveryReceiver:
    """Listens for HELLO packets on a UDP socket, accumulates discoveries."""

    def __init__(self, port: int):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(('', port))
        self._sock.setblocking(False)
        self._discoveries: list[tuple[str, str, int]] = []  # (uid, host, tcp_port)

    def poll(self) -> None:
        """Non-blocking drain of the socket. Parse HELLOs, record discoveries."""
        while True:
            try:
                data, (host, _port) = self._sock.recvfrom(512)
            except BlockingIOError:
                break
            parsed = parse_hello(data)
            if parsed is None:
                continue
            uid, tcp_port = parsed
            self._discoveries.append((uid, host, tcp_port))

    def drain_discoveries(self) -> list[tuple[str, str, int]]:
        """Return and clear accumulated discoveries."""
        out = self._discoveries[:]
        self._discoveries.clear()
        return out

    def close(self) -> None:
        self._sock.close()
