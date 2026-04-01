"""Runtime integration test for simulator configuration logging."""

import os
import queue
import re
import socket
import subprocess
import threading
import time

import pytest

from elemctl.device_protocol import ACK_OK, encode_attach, encode_set_profile, parse_ack

from .sim_helpers import NETWORK_SIM_BIN, find_free_udp_port

pytestmark = pytest.mark.runtime_integration


class _StdoutCollector:
    def __init__(self, proc):
        self._proc = proc
        self._queue: queue.Queue[str] = queue.Queue()
        self.lines: list[str] = []
        self._thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._thread.start()

    def _read_stdout(self) -> None:
        for line in self._proc.stdout:
            self.lines.append(line)
            self._queue.put(line)

    def wait_for(self, pattern: str, timeout: float = 5.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for line in self.lines:
                if pattern in line:
                    return line
            try:
                line = self._queue.get(timeout=min(0.1, max(0.01, deadline - time.monotonic())))
            except queue.Empty:
                continue
            if pattern in line:
                return line
        raise AssertionError(
            f'expected stdout line containing {pattern!r}; saw:\n' + ''.join(self.lines)
        )


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise AssertionError(f'socket closed while waiting for {n} bytes')
        buf.extend(chunk)
    return bytes(buf)


def test_network_sim_logs_profile_and_attach_configuration():
    if not NETWORK_SIM_BIN.exists():
        pytest.skip(f'network_sim not built at {NETWORK_SIM_BIN}')

    discovery_port = find_free_udp_port()
    frame_port = find_free_udp_port()
    device_uid = f'log-test-sim-{os.getpid()}'
    proc = subprocess.Popen(
        [
            str(NETWORK_SIM_BIN),
            '--tcp-port', '0',
            '--device-uid', device_uid,
            '--discovery-port', str(discovery_port),
            '--discovery-host', '127.0.0.1',
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    collector = _StdoutCollector(proc)
    sock = None
    try:
        listen_line = collector.wait_for('network_sim: listening on tcp=')
        match = re.search(r'network_sim: listening on tcp=(\d+)', listen_line)
        assert match is not None, listen_line
        tcp_port = int(match.group(1))

        collector.wait_for('Waiting for controller on port')
        sock = socket.create_connection(('127.0.0.1', tcp_port), timeout=5.0)
        sock.sendall(encode_set_profile(8))
        assert parse_ack(_recv_exact(sock, 6)) == ACK_OK

        collector.wait_for('Controller connected.')
        collector.wait_for('network_sim: set_profile ok strip_length=8')

        sock.sendall(encode_attach(7, frame_port))
        assert parse_ack(_recv_exact(sock, 6)) == ACK_OK
        collector.wait_for(
            f'network_sim: attach ok device_id=7 frame_port={frame_port} controller=127.0.0.1'
        )
    finally:
        if sock is not None:
            sock.close()
        proc.terminate()
        try:
            proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3.0)
