"""Unit tests for elemctl config loading."""

import json

import pytest

from elemctl.config import (
    Config, ConfigError, DEFAULT_CONFIG_PATH, DEFAULT_DISCOVERY_PORT,
    DEFAULT_LOGS_PATH, DeviceConfig, load_config, resolve_config_path,
    resolve_runtime_path,
)


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
            _valid_device(device_id=1, strip_id="main", tcp_port=9001,
                          device_uid="uid-1"),
            _valid_device(device_id=2, strip_id="main", tcp_port=9002,
                          device_uid="uid-2"),
        ])
        with pytest.raises(ConfigError, match="duplicate strip_id"):
            load_config(_write_config(tmp_path, data))

    def test_duplicate_endpoint(self, tmp_path):
        data = _valid_config(devices=[
            _valid_device(device_id=1, strip_id="a", host="127.0.0.1", tcp_port=9001,
                          device_uid="uid-1"),
            _valid_device(device_id=2, strip_id="b", host="127.0.0.1", tcp_port=9001,
                          device_uid="uid-2"),
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

    # --- discovery_port ---

    def test_discovery_port_parsed(self, tmp_path):
        data = _valid_config()
        data["controller"]["discovery_port"] = 9999
        cfg = load_config(_write_config(tmp_path, data))
        assert cfg.discovery_port == 9999

    def test_discovery_port_absent_uses_default(self, tmp_path):
        cfg = load_config(_write_config(tmp_path, _valid_config()))
        assert cfg.discovery_port == DEFAULT_DISCOVERY_PORT

    def test_logs_dir_parsed(self, tmp_path):
        data = _valid_config()
        data["controller"]["logs_dir"] = "~/elemctl-logs"
        cfg = load_config(_write_config(tmp_path, data))
        assert cfg.logs_dir == "~/elemctl-logs"

    def test_logs_dir_wrong_type(self, tmp_path):
        data = _valid_config()
        data["controller"]["logs_dir"] = 123
        with pytest.raises(ConfigError, match="logs_dir"):
            load_config(_write_config(tmp_path, data))

    def test_discovery_port_null_disables_discovery(self, tmp_path):
        data = _valid_config()
        data["controller"]["discovery_port"] = None
        cfg = load_config(_write_config(tmp_path, data))
        assert cfg.discovery_port is None

    def test_discovery_port_wrong_type(self, tmp_path):
        data = _valid_config()
        data["controller"]["discovery_port"] = "9999"
        with pytest.raises(ConfigError, match="integer or null"):
            load_config(_write_config(tmp_path, data))

    def test_discovery_port_out_of_range(self, tmp_path):
        data = _valid_config()
        data["controller"]["discovery_port"] = 0
        with pytest.raises(ConfigError, match="1-65535"):
            load_config(_write_config(tmp_path, data))

    def test_discovery_allows_empty_host(self, tmp_path):
        data = _valid_config()
        data["controller"]["discovery_port"] = 9999
        data["devices"] = [_valid_device(host="", tcp_port=0)]
        cfg = load_config(_write_config(tmp_path, data))
        assert cfg.devices[0].host == ""
        assert cfg.devices[0].tcp_port == 0

    def test_discovery_default_allows_empty_host(self, tmp_path):
        data = _valid_config(devices=[_valid_device(host="", tcp_port=0)])
        cfg = load_config(_write_config(tmp_path, data))
        assert cfg.devices[0].host == ""
        assert cfg.devices[0].tcp_port == 0

    def test_discovery_disabled_requires_host(self, tmp_path):
        data = _valid_config(devices=[_valid_device(tcp_port=0)])
        data["controller"]["discovery_port"] = None
        with pytest.raises(ConfigError, match="tcp_port"):
            load_config(_write_config(tmp_path, data))

    def test_discovery_mismatched_empty_host_nonempty_port(self, tmp_path):
        """host="" with tcp_port=9001 is invalid even with discovery enabled."""
        data = _valid_config()
        data["controller"]["discovery_port"] = 9999
        data["devices"] = [_valid_device(host="", tcp_port=9001)]
        with pytest.raises(ConfigError, match="both be empty"):
            load_config(_write_config(tmp_path, data))

    def test_no_discovery_empty_host_rejected(self, tmp_path):
        """host="" is invalid without discovery."""
        data = _valid_config(devices=[_valid_device(host="", tcp_port=9001)])
        data["controller"]["discovery_port"] = None
        with pytest.raises(ConfigError, match="host must be non-empty"):
            load_config(_write_config(tmp_path, data))

    def test_empty_device_uid_rejected(self, tmp_path):
        data = _valid_config(devices=[_valid_device(device_uid="")])
        with pytest.raises(ConfigError, match="device_uid must be non-empty"):
            load_config(_write_config(tmp_path, data))

    # --- device_uid uniqueness ---

    def test_duplicate_device_uid_rejected(self, tmp_path):
        data = _valid_config(devices=[
            _valid_device(device_id=1, strip_id="a", tcp_port=9001,
                          device_uid="same-uid"),
            _valid_device(device_id=2, strip_id="b", tcp_port=9002,
                          device_uid="same-uid"),
        ])
        with pytest.raises(ConfigError, match="duplicate device_uid"):
            load_config(_write_config(tmp_path, data))


class TestResolveConfigPath:
    def test_returns_path_for_existing_file(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text("{}")
        assert resolve_config_path(str(p)) == str(p)

    def test_raises_for_nonexistent_file(self):
        with pytest.raises(ConfigError, match="config file not found"):
            resolve_config_path("/nonexistent/config.json")

    def test_error_mentions_default_path(self):
        with pytest.raises(ConfigError, match=DEFAULT_CONFIG_PATH):
            resolve_config_path("/nonexistent/config.json")

    def test_expands_tilde(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        cfg = tmp_path / "my_config.json"
        cfg.write_text("{}")
        result = resolve_config_path("~/my_config.json")
        assert result == str(cfg)
        assert "~" not in result


class TestResolveRuntimePath:
    def test_prefers_cli_override(self):
        assert resolve_runtime_path("~/cli", "~/cfg", DEFAULT_LOGS_PATH).endswith("/cli")

    def test_falls_back_to_config(self):
        assert resolve_runtime_path(None, "~/cfg", DEFAULT_LOGS_PATH).endswith("/cfg")

    def test_uses_default(self):
        assert resolve_runtime_path(None, None, DEFAULT_LOGS_PATH) == DEFAULT_LOGS_PATH


class TestServerMainDefaults:
    """Smoke tests verifying server.main() exposes default args via argparse."""

    def test_no_args_exits_with_config_error(self, monkeypatch):
        """Zero-arg invocation fails with a clear config-not-found message."""
        from elemctl.server import main as server_main

        monkeypatch.setattr(
            'sys.argv',
            ['elemctl.server', '--config', '/definitely/missing/config.json'],
        )
        # Missing config path should exit(1) with config error
        with pytest.raises(SystemExit) as exc_info:
            server_main()
        assert exc_info.value.code == 1

    def test_help_shows_defaults(self, capsys, monkeypatch):
        """--help output includes the default paths."""
        monkeypatch.setattr('sys.argv', ['elemctl.server', '--help'])
        with pytest.raises(SystemExit) as exc_info:
            from elemctl.server import main as server_main
            server_main()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert DEFAULT_CONFIG_PATH in out
        from elemctl.config import DEFAULT_SOCKET_PATH
        assert DEFAULT_SOCKET_PATH in out
