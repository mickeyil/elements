"""Production client for connecting to the controller service over a unix socket."""

from __future__ import annotations

import itertools
import socket

from .controller_protocol import (
    PROTOCOL_VERSION,
    ROLE_WRITER,
    ProtocolReader,
    encode_json,
    parse_json_payload,
)


class ControllerClient:
    """Blocking controller client over a unix socket. Caller gates readability via select before recv_once()."""

    def __init__(self, socket_path: str, timeout: float = 2.0, role: str = ROLE_WRITER):
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(timeout)
        self._sock.connect(socket_path)
        self._reader = ProtocolReader()
        self._id_counter = itertools.count(1)
        self._prefetched: list[tuple[int, bytes]] = []
        self._send_hello(role)

    def fileno(self) -> int:
        """Expose socket fd for use with select.select()."""
        return self._sock.fileno()

    def send_cmd(self, cmd: dict) -> None:
        self._sock.sendall(encode_json(cmd))

    def next_id(self) -> int:
        """Return the next client-local command id."""
        return next(self._id_counter)

    def has_buffered_messages(self) -> bool:
        """True when recv_once() can return without reading the socket."""
        return bool(getattr(self, '_prefetched', None))

    def recv_once(self) -> list[tuple[int, bytes]]:
        """Single blocking recv. Caller must gate with select first."""
        prefetched = getattr(self, '_prefetched', None)
        if prefetched:
            out = self._prefetched
            self._prefetched = []
            return out
        data = self._sock.recv(4096)
        if not data:
            raise ConnectionError("server closed connection")
        self._reader.feed(data)
        return self._reader.messages()

    def close(self) -> None:
        self._sock.close()

    def _send_hello(self, role: str) -> None:
        self.send_cmd({
            'id': 0,
            'cmd': 'hello',
            'role': role,
            'protocol_version': PROTOCOL_VERSION,
        })

        while True:
            messages = self.recv_once()
            keep: list[tuple[int, bytes]] = []
            for i, (kind, payload) in enumerate(messages):
                if kind != 0x01:
                    keep.append((kind, payload))
                    continue
                msg = parse_json_payload(payload)
                if msg.get('type') != 'reply' or msg.get('id') != 0:
                    keep.append((kind, payload))
                    continue
                if not msg.get('ok'):
                    raise ConnectionError(msg.get('error', 'hello failed'))
                keep.extend(messages[i + 1:])
                self._prefetched.extend(keep)
                return
            self._prefetched.extend(keep)
