import subprocess
from pathlib import Path

from .sim_helpers import start_sim


class _FakeProc:
    def __init__(self):
        self.stdout = []
        self.stderr = []

    def kill(self) -> None:
        pass

    def wait(self, timeout=None) -> None:
        pass


class _FakeSimProcess:
    def __init__(self, proc, tcp_port, frame_port):
        self.proc = proc
        self.tcp_port = tcp_port
        self.frame_port = frame_port

    def wait_ready(self, timeout: float = 5.0) -> bool:
        return True


class TestSimHelpers:
    def test_start_sim_always_passes_explicit_discovery_port(self, monkeypatch):
        called = {}

        monkeypatch.setattr('tests.sim_helpers.NETWORK_SIM_BIN', Path('build/network_sim'))
        monkeypatch.setattr('tests.sim_helpers.find_free_udp_port', lambda: 45678)
        monkeypatch.setattr('tests.sim_helpers.SimProcess', _FakeSimProcess)

        def fake_popen(cmd, stdout=None, stderr=None):
            called['cmd'] = cmd
            assert stdout is subprocess.PIPE
            assert stderr is subprocess.PIPE
            return _FakeProc()

        monkeypatch.setattr('tests.sim_helpers.subprocess.Popen', fake_popen)

        sim = start_sim(12345, 23456, 10, device_id=7)

        assert sim.tcp_port == 12345
        assert sim.frame_port == 23456
        assert called['cmd'] == [
            'build/network_sim',
            '--tcp-port', '12345',
            '--device-uid', 'sim-7',
            '--discovery-port', '45678',
        ]

    def test_start_sim_preserves_explicit_discovery_port(self, monkeypatch):
        called = {}

        monkeypatch.setattr('tests.sim_helpers.NETWORK_SIM_BIN', Path('build/network_sim'))
        monkeypatch.setattr('tests.sim_helpers.SimProcess', _FakeSimProcess)

        def fake_popen(cmd, stdout=None, stderr=None):
            called['cmd'] = cmd
            return _FakeProc()

        monkeypatch.setattr('tests.sim_helpers.subprocess.Popen', fake_popen)

        start_sim(12345, 23456, 10, device_id=7, discovery_port=60001, discovery_host='127.0.0.1')

        assert called['cmd'] == [
            'build/network_sim',
            '--tcp-port', '12345',
            '--device-uid', 'sim-7',
            '--discovery-port', '60001',
            '--discovery-host', '127.0.0.1',
        ]
