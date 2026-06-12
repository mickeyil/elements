"""Unit tests for elemctl config loading (v3 schema)."""

import json

import pytest

from elemctl.config import (
    ConfigError,
    DEFAULT_DISCOVERY_PORT,
    DEFAULT_FRAME_PORT,
    DEFAULT_LINK_PORT,
    DEFAULT_SYNC_PORT,
    MAX_DEVICE_PIXELS,
    default_config_doc,
    load_config,
    load_config_obj,
    resolve_config_path,
    resolve_runtime_path,
    validate_device_uid,
)


def _write_config(tmp_path, data):
    """Write a config dict as JSON and return the path."""
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data))
    return str(p)


def _valid_device(**overrides):
    """Return a valid device dict with optional overrides."""
    d = {
        "device_uid": "sim-left",
        "strip_id": "main_left",
        "length": 150,
    }
    d.update(overrides)
    return d


def _valid_config(**overrides):
    """Return a valid config dict with optional overrides."""
    c = {
        "controller": {},
        "devices": [_valid_device()],
    }
    c.update(overrides)
    return c


# ---------------------------------------------------------------------------
# Controller section
# ---------------------------------------------------------------------------

def test_ports_default_to_well_known_values(tmp_path):
    config = load_config(_write_config(tmp_path, _valid_config()))
    assert config.discovery_port == DEFAULT_DISCOVERY_PORT == 6040
    assert config.link_port == DEFAULT_LINK_PORT == 6041
    assert config.frame_port == DEFAULT_FRAME_PORT == 6042
    assert config.sync_port == DEFAULT_SYNC_PORT == 6043


def test_explicit_ports_override_defaults(tmp_path):
    doc = _valid_config(controller={
        "discovery_port": 7040,
        "link_port": 7041,
        "frame_port": 7042,
        "sync_port": 7043,
    })
    config = load_config(_write_config(tmp_path, doc))
    assert (config.discovery_port, config.link_port,
            config.frame_port, config.sync_port) == (7040, 7041, 7042, 7043)


@pytest.mark.parametrize('field', [
    'discovery_port', 'link_port', 'frame_port', 'sync_port',
])
@pytest.mark.parametrize('bad', [0, 65536, -1, 'x', None, True])
def test_invalid_port_rejected(tmp_path, field, bad):
    doc = _valid_config(controller={field: bad})
    with pytest.raises(ConfigError):
        load_config(_write_config(tmp_path, doc))


def test_duplicate_ports_rejected(tmp_path):
    doc = _valid_config(controller={"frame_port": 6043})
    with pytest.raises(ConfigError, match='duplicates'):
        load_config(_write_config(tmp_path, doc))


def test_missing_controller_section_rejected(tmp_path):
    with pytest.raises(ConfigError, match='controller'):
        load_config(_write_config(tmp_path, {"devices": []}))


def test_optional_dirs(tmp_path):
    doc = _valid_config(controller={
        "animations_dir": "/tmp/anims",
        "logs_dir": "/tmp/logs",
    })
    config = load_config(_write_config(tmp_path, doc))
    assert config.animations_dir == "/tmp/anims"
    assert config.logs_dir == "/tmp/logs"

    doc = _valid_config(controller={"animations_dir": 5})
    with pytest.raises(ConfigError):
        load_config(_write_config(tmp_path, doc))


def test_default_doc_round_trips():
    config = load_config_obj(default_config_doc())
    assert config.devices == []
    assert config.discovery_port == 6040


# ---------------------------------------------------------------------------
# Device entries
# ---------------------------------------------------------------------------

def test_devices_loaded_with_inferred_type(tmp_path):
    doc = _valid_config(devices=[
        _valid_device(),
        _valid_device(device_uid='esp-aabbccddeeff', strip_id='right',
                      length=30, label='porch'),
    ])
    config = load_config(_write_config(tmp_path, doc))
    sim, esp = config.devices
    assert sim.device_uid == 'sim-left'
    assert sim.device_type == 'sim'
    assert sim.label is None
    assert esp.device_type == 'esp32'
    assert esp.label == 'porch'


def test_empty_devices_allowed(tmp_path):
    config = load_config(_write_config(tmp_path, _valid_config(devices=[])))
    assert config.devices == []


def test_missing_devices_section_rejected(tmp_path):
    with pytest.raises(ConfigError, match='devices'):
        load_config(_write_config(tmp_path, {"controller": {}}))


@pytest.mark.parametrize('bad_uid', [
    None, '', 5, 'left',                  # missing or no prefix
    'sim-',                               # nothing after the prefix
    'sim-3456789012345',                  # 17 bytes, over the wire slot
    'esp-AABBCCDDEEFF',                   # uppercase hex
    'esp-aabbcc',                         # short hex
    'esp-aabbccddeeff00',                 # long hex
    'sim-éclair',                    # not ASCII
])
def test_invalid_device_uid_rejected(tmp_path, bad_uid):
    doc = _valid_config(devices=[_valid_device(device_uid=bad_uid)])
    with pytest.raises(ConfigError):
        load_config(_write_config(tmp_path, doc))


def test_full_width_sim_uid_accepted(tmp_path):
    uid = 'sim-345678901234'  # exactly 16 bytes
    doc = _valid_config(devices=[_valid_device(device_uid=uid)])
    config = load_config(_write_config(tmp_path, doc))
    assert config.devices[0].device_uid == uid


def test_validate_device_uid_messages():
    assert validate_device_uid('sim-a') is None
    assert validate_device_uid('esp-aabbccddeeff') is None
    assert validate_device_uid('nope') is not None
    assert validate_device_uid('') is not None


@pytest.mark.parametrize('bad', [None, '', 7, 'bad strip!', 'a/b'])
def test_invalid_strip_id_rejected(tmp_path, bad):
    doc = _valid_config(devices=[_valid_device(strip_id=bad)])
    with pytest.raises(ConfigError):
        load_config(_write_config(tmp_path, doc))


@pytest.mark.parametrize('bad', [0, -3, MAX_DEVICE_PIXELS + 1, 'x', None, True])
def test_invalid_length_rejected(tmp_path, bad):
    doc = _valid_config(devices=[_valid_device(length=bad)])
    with pytest.raises(ConfigError):
        load_config(_write_config(tmp_path, doc))


@pytest.mark.parametrize('bad', ['', 7, True])
def test_invalid_label_rejected(tmp_path, bad):
    doc = _valid_config(devices=[_valid_device(label=bad)])
    with pytest.raises(ConfigError):
        load_config(_write_config(tmp_path, doc))


def test_duplicate_uid_rejected(tmp_path):
    doc = _valid_config(devices=[_valid_device(), _valid_device(strip_id='other')])
    with pytest.raises(ConfigError, match='duplicate device_uid'):
        load_config(_write_config(tmp_path, doc))


def test_shared_strip_id_requires_same_length(tmp_path):
    doc = _valid_config(devices=[
        _valid_device(),
        _valid_device(device_uid='sim-right', length=10),
    ])
    with pytest.raises(ConfigError, match='different length'):
        load_config(_write_config(tmp_path, doc))

    doc = _valid_config(devices=[
        _valid_device(),
        _valid_device(device_uid='sim-right'),
    ])
    config = load_config(_write_config(tmp_path, doc))
    assert len(config.devices) == 2


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def test_resolve_config_path_requires_explicit_file(tmp_path):
    missing = tmp_path / 'nope.json'
    with pytest.raises(ConfigError, match='not found'):
        resolve_config_path(str(missing))

    path = _write_config(tmp_path, _valid_config())
    assert resolve_config_path(path) == path


def test_resolve_runtime_path_precedence():
    assert resolve_runtime_path('/cli', '/config', '/default') == '/cli'
    assert resolve_runtime_path(None, '/config', '/default') == '/config'
    assert resolve_runtime_path(None, None, '/default') == '/default'
