"""Shared UDS test helpers for controller integration tests."""

import socket
import time
from pathlib import Path

from elemctl.uds_wire import UdsReader, encode_json


class UdsClient:
    """Test helper for talking to a UdsServer."""

    def __init__(self, socket_path: str, timeout: float = 2.0):
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(timeout)
        self._sock.connect(socket_path)
        self._reader = UdsReader()

    def send_cmd(self, cmd: dict) -> None:
        self._sock.sendall(encode_json(cmd))

    def recv_messages(self, timeout: float = 1.0) -> list[tuple[int, bytes]]:
        """Receive messages until timeout. Returns all collected messages."""
        all_msgs: list[tuple[int, bytes]] = []
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


def wait_for_socket(path: str, timeout: float = 2.0) -> bool:
    """Wait until the UDS socket file exists."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if Path(path).exists():
            return True
        time.sleep(0.01)
    return False
