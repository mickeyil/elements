"""Shared helpers for integration tests that use network_sim subprocesses."""

import signal
import socket
import subprocess
import threading
from pathlib import Path

import pytest

_repo = Path(__file__).resolve().parent.parent.parent
NETWORK_SIM_BIN = _repo / 'build' / 'network_sim'


def find_free_tcp_port() -> int:
    """Bind a TCP socket to port 0, read back the assigned port, close."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def find_free_udp_port() -> int:
    """Bind a UDP socket to port 0, read back the assigned port, close."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(('', 0))
    port = s.getsockname()[1]
    s.close()
    return port


class SimProcess:
    """Manages a network_sim subprocess with stdout-based readiness detection."""

    def __init__(self, proc: subprocess.Popen, tcp_port: int, frame_port: int):
        self.proc = proc
        self.tcp_port = tcp_port
        self.frame_port = frame_port
        self._stderr_lines: list[bytes] = []
        self._ready_event = threading.Event()
        self._stdout_reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_reader.start()
        self._stderr_reader.start()

    def _read_stdout(self) -> None:
        for line in self.proc.stdout:
            if b'Waiting for controller' in line:
                self._ready_event.set()

    def _read_stderr(self) -> None:
        for line in self.proc.stderr:
            self._stderr_lines.append(line)

    def wait_ready(self, timeout: float = 5.0) -> bool:
        """Wait for network_sim to be in accept(). Reusable across reconnects."""
        result = self._ready_event.wait(timeout=timeout)
        self._ready_event.clear()
        return result

    def dump_stderr(self) -> str:
        """Return collected stderr as a string (for diagnostics on failure)."""
        return b''.join(self._stderr_lines).decode(errors='replace')


def start_sim(tcp_port: int, frame_port: int, strip_length: int,
              device_id: int, *, discovery_port: int | None = None,
              discovery_host: str | None = None,
              device_uid: str | None = None) -> SimProcess:
    """Start a network_sim subprocess and wait for readiness."""
    if not NETWORK_SIM_BIN.exists():
        pytest.skip(f'network_sim not built at {NETWORK_SIM_BIN}')

    cmd = [
        str(NETWORK_SIM_BIN),
        '--tcp-port', str(tcp_port),
        '--frame-port', str(frame_port),
        '--strip-length', str(strip_length),
        '--device-id', str(device_id),
    ]
    if discovery_port is not None:
        cmd += ['--discovery-port', str(discovery_port)]
    if discovery_host is not None:
        cmd += ['--discovery-host', discovery_host]
    if device_uid is not None:
        cmd += ['--device-uid', device_uid]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    sim = SimProcess(proc, tcp_port, frame_port)
    if not sim.wait_ready():
        proc.kill()
        proc.wait()
        pytest.fail('network_sim did not start listening in time')

    return sim


def stop_sim(sim: SimProcess) -> None:
    """SIGTERM a network_sim process and wait for exit."""
    sim.proc.send_signal(signal.SIGTERM)
    try:
        sim.proc.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        sim.proc.kill()
        sim.proc.wait()
