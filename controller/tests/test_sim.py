"""Tests for elemctl.sim command building."""

import pytest

from elemctl.config import Config, DEFAULT_DISCOVERY_PORT, DeviceConfig
from elemctl.sim import build_sim_command


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

        cmd = build_sim_command(
            _config(),
            'sim-1',
            network_sim_bin=fake_bin,
        )

        assert cmd == [
            str(fake_bin),
            '--tcp-port', '0',
            '--frame-port', '9002',
            '--strip-length', '60',
            '--device-id', '1',
            '--discovery-port', str(DEFAULT_DISCOVERY_PORT),
            '--discovery-host', '127.0.0.1',
            '--device-uid', 'sim-1',
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
