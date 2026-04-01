"""Shared controller integration test helpers."""

import itertools
import socket
import time
from pathlib import Path

from elemctl.controller_protocol import (
    PROTOCOL_VERSION,
    ROLE_WRITER,
    ProtocolReader,
    encode_json,
    parse_json_payload,
)


class ControllerClient:
    """Test helper for talking to a ControllerServer."""

    def __init__(self, socket_path: str, timeout: float = 2.0, role: str = ROLE_WRITER):
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(timeout)
        self._sock.connect(socket_path)
        self._reader = ProtocolReader()
        self._id_counter = itertools.count(1)
        self._prefetched: list[tuple[int, bytes]] = []
        self._send_hello(role)

    def send_cmd(self, cmd: dict) -> None:
        self._sock.sendall(encode_json(cmd))

    def next_id(self) -> int:
        return next(self._id_counter)

    def recv_messages(self, timeout: float = 1.0) -> list[tuple[int, bytes]]:
        """Receive messages until timeout. Returns all collected messages."""
        all_msgs: list[tuple[int, bytes]] = self._prefetched[:]
        self._prefetched.clear()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                self._sock.settimeout(max(0.01, deadline - time.monotonic()))
                data = self._sock.recv(4096)
            except socket.timeout:
                # No data right now — if we have messages, return them
                if all_msgs:
                    break
                continue
            except OSError:
                break
            if not data:
                break
            self._reader.feed(data)
            all_msgs.extend(self._reader.messages())
        all_msgs.extend(self._reader.messages())
        return all_msgs

    def close(self) -> None:
        self._sock.close()

    def _send_hello(self, role: str) -> None:
        self.send_cmd({
            'id': 0,
            'cmd': 'hello',
            'role': role,
            'protocol_version': PROTOCOL_VERSION,
        })

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            msgs = self.recv_messages(timeout=max(0.05, deadline - time.monotonic()))
            keep: list[tuple[int, bytes]] = []
            for i, (kind, payload) in enumerate(msgs):
                if kind != 0x01:
                    keep.append((kind, payload))
                    continue
                msg = parse_json_payload(payload)
                if msg.get('type') != 'reply' or msg.get('id') != 0:
                    keep.append((kind, payload))
                    continue
                if not msg.get('ok'):
                    raise ConnectionError(msg.get('error', 'hello failed'))
                keep.extend(msgs[i + 1:])
                self._prefetched.extend(keep)
                return
            self._prefetched.extend(keep)
        raise ConnectionError('hello timed out')


def wait_for_socket(path: str, timeout: float = 2.0) -> bool:
    """Wait until the unix socket file exists."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if Path(path).exists():
            return True
        time.sleep(0.01)
    return False
