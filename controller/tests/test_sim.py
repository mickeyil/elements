"""Tests for elemctl.sim command building."""

import subprocess

import pytest

from elemctl.config import Config, DEFAULT_DISCOVERY_PORT, DeviceConfig
from elemctl import sim
from elemctl.sim import build_sim_command, ensure_network_sim_built


def _device(**overrides) -> DeviceConfig:
    data = dict(
        device_id=1,
        device_uid='sim-1',
        device_type='sim',
        host='',
        tcp_port=0,
        strip_id='main',
        length=60,
    )
    data.update(overrides)
    return DeviceConfig(**data)


def _config(*devices: DeviceConfig, discovery_port: int | None = DEFAULT_DISCOVERY_PORT) -> Config:
    return Config(
        frame_port=9002,
        discovery_port=discovery_port,
        devices=list(devices) or [_device()],
    )


class TestBuildSimCommand:
    def test_happy_path(self, tmp_path):
        fake_bin = tmp_path / 'network_sim'
        fake_bin.write_text('')
        log_file = tmp_path / 'logs' / 'sim-1.log'

        cmd = build_sim_command(
            _config(),
            'sim-1',
            log_file=str(log_file),
            network_sim_bin=fake_bin,
        )

        assert cmd == [
            str(fake_bin),
            '--tcp-port', '0',
            '--discovery-port', str(DEFAULT_DISCOVERY_PORT),
            '--discovery-host', '127.0.0.1',
            '--device-uid', 'sim-1',
            '--log-file', str(log_file),
        ]

    def test_overrides_discovery_host_and_tcp_port(self, tmp_path):
        fake_bin = tmp_path / 'network_sim'
        fake_bin.write_text('')

        cmd = build_sim_command(
            _config(),
            'sim-1',
            discovery_host='192.168.1.10',
            tcp_port=12345,
            network_sim_bin=fake_bin,
        )

        assert '--discovery-host' in cmd
        assert '192.168.1.10' in cmd
        assert '--tcp-port' in cmd
        assert '12345' in cmd

    def test_unknown_uid_rejected(self, tmp_path):
        fake_bin = tmp_path / 'network_sim'
        fake_bin.write_text('')

        with pytest.raises(ValueError, match='unknown sim device_uid'):
            build_sim_command(_config(), 'missing', network_sim_bin=fake_bin)

    def test_non_sim_uid_rejected(self, tmp_path):
        fake_bin = tmp_path / 'network_sim'
        fake_bin.write_text('')
        cfg = _config(_device(device_uid='esp-1', device_type='esp32'))

        with pytest.raises(ValueError, match='not "sim"'):
            build_sim_command(cfg, 'esp-1', network_sim_bin=fake_bin)

    def test_discovery_disabled_rejected(self, tmp_path):
        fake_bin = tmp_path / 'network_sim'
        fake_bin.write_text('')

        with pytest.raises(ValueError, match='disabled'):
            build_sim_command(
                _config(discovery_port=None),
                'sim-1',
                network_sim_bin=fake_bin,
            )

    def test_missing_binary_rejected(self, tmp_path):
        missing = tmp_path / 'missing_network_sim'

        with pytest.raises(ValueError, match='network_sim not built'):
            build_sim_command(_config(), 'sim-1', network_sim_bin=missing)

    def test_invalid_tcp_port_rejected(self, tmp_path):
        fake_bin = tmp_path / 'network_sim'
        fake_bin.write_text('')

        with pytest.raises(ValueError, match='0-65535'):
            build_sim_command(
                _config(),
                'sim-1',
                tcp_port=70000,
                network_sim_bin=fake_bin,
            )


class TestEnsureNetworkSimBuilt:
    def test_invokes_cmake_build_for_network_sim(self, monkeypatch, tmp_path):
        build_dir = tmp_path / 'build'
        build_dir.mkdir()
        (build_dir / 'CMakeCache.txt').write_text('# fake cache\n')
        calls = []

        def fake_run(cmd, check, cwd):
            calls.append((cmd, check, cwd))

        monkeypatch.setattr(sim.subprocess, 'run', fake_run)

        ensure_network_sim_built(build_dir=build_dir)

        assert calls == [(
            ['cmake', '--build', str(build_dir), '--target', 'network_sim'],
            True,
            sim.REPO_ROOT,
        )]

    def test_requires_configured_build_dir(self, tmp_path):
        build_dir = tmp_path / 'build'
        build_dir.mkdir()

        with pytest.raises(ValueError, match='run `cmake -B build` first'):
            ensure_network_sim_built(build_dir=build_dir)

    def test_wraps_cmake_build_failures(self, monkeypatch, tmp_path):
        build_dir = tmp_path / 'build'
        build_dir.mkdir()
        (build_dir / 'CMakeCache.txt').write_text('# fake cache\n')

        def fake_run(cmd, check, cwd):
            raise subprocess.CalledProcessError(returncode=1, cmd=cmd)

        monkeypatch.setattr(sim.subprocess, 'run', fake_run)

        with pytest.raises(ValueError, match='failed to build network_sim'):
            ensure_network_sim_built(build_dir=build_dir)


class TestSimMain:
    def test_builds_before_exec(self, monkeypatch, tmp_path):
        cfg = _config()
        events = []

        class ExecCalled(Exception):
            pass

        def fake_ensure() -> None:
            events.append('build')

        def fake_build_sim_command(config, device_uid, **kwargs):
            events.append(('cmd', device_uid, kwargs))
            return ['/tmp/network_sim', '--tcp-port', '0']

        def fake_execv(path, argv):
            events.append(('exec', path, argv))
            raise ExecCalled

        monkeypatch.setattr(sim, 'ensure_network_sim_built', fake_ensure)
        monkeypatch.setattr(sim, 'build_sim_command', fake_build_sim_command)
        monkeypatch.setattr(sim, 'resolve_config_path', lambda _: tmp_path / 'controller.json')
        monkeypatch.setattr(sim, 'load_config', lambda _: cfg)
        monkeypatch.setattr(sim, 'resolve_runtime_path', lambda *_: str(tmp_path / 'logs'))
        monkeypatch.setattr(sim.os, 'execv', fake_execv)
        monkeypatch.setattr(
            sim.sys,
            'argv',
            ['elemctl', 'sim-1', '--config', str(tmp_path / 'controller.json')],
        )

        with pytest.raises(ExecCalled):
            sim.main()

        assert events == [
            'build',
            (
                'cmd',
                'sim-1',
                {
                    'discovery_host': '127.0.0.1',
                    'tcp_port': 0,
                    'log_file': str(tmp_path / 'logs' / 'sim-1.log'),
                },
            ),
            ('exec', '/tmp/network_sim', ['/tmp/network_sim', '--tcp-port', '0']),
        ]
