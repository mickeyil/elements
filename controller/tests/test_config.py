"""Unit tests for elemctl config loading."""

import json

import pytest

from elemctl.config import Config, ConfigError, DeviceConfig, load_config


def _write_config(tmp_path, data):
    """Write a config dict as JSON and return the path."""
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data))
    return str(p)


def _valid_device(**overrides):
    """Return a valid device dict with optional overrides."""
    d = {
        "device_id": 1,
        "device_uid": "sim-left",
        "device_type": "sim",
        "host": "127.0.0.1",
        "tcp_port": 9001,
        "strip_id": "main_left",
        "length": 150,
    }
    d.update(overrides)
    return d


def _valid_config(**overrides):
    """Return a valid config dict with optional overrides."""
    c = {
        "controller": {"frame_port": 9000},
        "devices": [_valid_device()],
    }
    c.update(overrides)
    return c


class TestLoadConfig:
    def test_single_device(self, tmp_path):
        path = _write_config(tmp_path, _valid_config())
        cfg = load_config(path)
        assert cfg.frame_port == 9000
        assert len(cfg.devices) == 1
        assert cfg.devices[0].device_id == 1
        assert cfg.devices[0].device_uid == "sim-left"
        assert cfg.devices[0].device_type == "sim"
        assert cfg.devices[0].host == "127.0.0.1"
        assert cfg.devices[0].tcp_port == 9001
        assert cfg.devices[0].strip_id == "main_left"
        assert cfg.devices[0].length == 150

    def test_multiple_devices(self, tmp_path):
        data = _valid_config(devices=[
            _valid_device(device_id=1, strip_id="left", tcp_port=9001),
            _valid_device(device_id=2, strip_id="right", tcp_port=9002,
                          device_uid="sim-right"),
        ])
        cfg = load_config(_write_config(tmp_path, data))
        assert len(cfg.devices) == 2
        assert cfg.devices[0].strip_id == "left"
        assert cfg.devices[1].strip_id == "right"

    def test_missing_controller(self, tmp_path):
        data = {"devices": [_valid_device()]}
        with pytest.raises(ConfigError, match="controller"):
            load_config(_write_config(tmp_path, data))

    def test_missing_devices(self, tmp_path):
        data = {"controller": {"frame_port": 9000}}
        with pytest.raises(ConfigError, match="devices"):
            load_config(_write_config(tmp_path, data))

    def test_empty_devices(self, tmp_path):
        data = _valid_config(devices=[])
        with pytest.raises(ConfigError, match="non-empty"):
            load_config(_write_config(tmp_path, data))

    def test_missing_frame_port(self, tmp_path):
        data = _valid_config()
        data["controller"] = {}
        with pytest.raises(ConfigError, match="frame_port"):
            load_config(_write_config(tmp_path, data))

    def test_missing_device_field(self, tmp_path):
        dev = _valid_device()
        del dev["strip_id"]
        data = _valid_config(devices=[dev])
        with pytest.raises(ConfigError, match="strip_id"):
            load_config(_write_config(tmp_path, data))

    def test_wrong_type_frame_port(self, tmp_path):
        data = _valid_config()
        data["controller"]["frame_port"] = "9000"
        with pytest.raises(ConfigError, match="integer"):
            load_config(_write_config(tmp_path, data))

    def test_wrong_type_tcp_port(self, tmp_path):
        dev = _valid_device(tcp_port="9001")
        data = _valid_config(devices=[dev])
        with pytest.raises(ConfigError, match="int"):
            load_config(_write_config(tmp_path, data))

    def test_invalid_device_type(self, tmp_path):
        dev = _valid_device(device_type="arduino")
        data = _valid_config(devices=[dev])
        with pytest.raises(ConfigError, match="device_type"):
            load_config(_write_config(tmp_path, data))

    def test_duplicate_device_id(self, tmp_path):
        data = _valid_config(devices=[
            _valid_device(device_id=1, strip_id="a", tcp_port=9001),
            _valid_device(device_id=1, strip_id="b", tcp_port=9002),
        ])
        with pytest.raises(ConfigError, match="duplicate device_id"):
            load_config(_write_config(tmp_path, data))

    def test_duplicate_strip_id(self, tmp_path):
        data = _valid_config(devices=[
            _valid_device(device_id=1, strip_id="main", tcp_port=9001),
            _valid_device(device_id=2, strip_id="main", tcp_port=9002),
        ])
        with pytest.raises(ConfigError, match="duplicate strip_id"):
            load_config(_write_config(tmp_path, data))

    def test_duplicate_endpoint(self, tmp_path):
        data = _valid_config(devices=[
            _valid_device(device_id=1, strip_id="a", host="127.0.0.1", tcp_port=9001),
            _valid_device(device_id=2, strip_id="b", host="127.0.0.1", tcp_port=9001),
        ])
        with pytest.raises(ConfigError, match="duplicate endpoint"):
            load_config(_write_config(tmp_path, data))

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/config.json")

    def test_invalid_json(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not valid json")
        with pytest.raises(json.JSONDecodeError):
            load_config(str(p))

    def test_bool_rejected_for_frame_port(self, tmp_path):
        data = _valid_config()
        data["controller"]["frame_port"] = True
        with pytest.raises(ConfigError, match="integer"):
            load_config(_write_config(tmp_path, data))

    def test_bool_rejected_for_device_id(self, tmp_path):
        dev = _valid_device()
        dev["device_id"] = False
        data = _valid_config(devices=[dev])
        with pytest.raises(ConfigError, match="int"):
            load_config(_write_config(tmp_path, data))

    def test_frame_port_out_of_range(self, tmp_path):
        data = _valid_config()
        data["controller"]["frame_port"] = 0
        with pytest.raises(ConfigError, match="1-65535"):
            load_config(_write_config(tmp_path, data))

    def test_tcp_port_out_of_range(self, tmp_path):
        dev = _valid_device(tcp_port=70000)
        data = _valid_config(devices=[dev])
        with pytest.raises(ConfigError, match="1-65535"):
            load_config(_write_config(tmp_path, data))

    def test_negative_device_id(self, tmp_path):
        dev = _valid_device(device_id=-1)
        data = _valid_config(devices=[dev])
        with pytest.raises(ConfigError, match="0-65535"):
            load_config(_write_config(tmp_path, data))

    def test_zero_length(self, tmp_path):
        dev = _valid_device(length=0)
        data = _valid_config(devices=[dev])
        with pytest.raises(ConfigError, match="length"):
            load_config(_write_config(tmp_path, data))
