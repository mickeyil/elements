"""Production UDS client for connecting to the controller service."""

from __future__ import annotations

import socket

from .uds_wire import UdsReader, encode_json


class UdsClient:
    """Blocking UDS client. Caller gates readability via select before recv_once()."""

    def __init__(self, socket_path: str, timeout: float = 2.0):
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(timeout)
        self._sock.connect(socket_path)
        self._reader = UdsReader()

    def fileno(self) -> int:
        """Expose socket fd for use with select.select()."""
        return self._sock.fileno()

    def send_cmd(self, cmd: dict) -> None:
        self._sock.sendall(encode_json(cmd))

    def recv_once(self) -> list[tuple[int, bytes]]:
        """Single blocking recv. Caller must gate with select first."""
        data = self._sock.recv(4096)
        if not data:
            raise ConnectionError("server closed connection")
        self._reader.feed(data)
        return self._reader.messages()

    def close(self) -> None:
        self._sock.close()
