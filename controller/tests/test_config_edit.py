"""Tests for editor-side config helpers."""

import json

import pytest

from elemctl.config import (
    DEFAULT_DISCOVERY_PORT,
    DEFAULT_FRAME_PORT,
    ConfigError,
    load_config,
    load_config_obj,
)
from elemctl.config_edit import (
    add_device,
    ensure_editor_shape,
    load_config_doc,
    make_device_entry,
    next_device_id,
    remove_device,
    save_config_doc,
)


def test_load_config_obj_validates_raw_dict():
    cfg = load_config_obj({
        "controller": {"frame_port": 9002},
        "devices": [{
            "device_id": 1,
            "device_uid": "sim-1",
            "device_type": "sim",
            "host": "",
            "tcp_port": 0,
            "strip_id": "main",
            "length": 60,
        }],
    })
    assert cfg.frame_port == 9002
    assert cfg.devices[0].device_uid == "sim-1"


def test_load_config_doc_missing_returns_skeleton(tmp_path):
    path = tmp_path / "missing.json"
    doc = load_config_doc(str(path))
    assert doc["controller"]["frame_port"] == DEFAULT_FRAME_PORT
    assert doc["controller"]["discovery_port"] == DEFAULT_DISCOVERY_PORT
    assert doc["devices"] == []


def test_ensure_editor_shape_preserves_unknown_fields():
    doc = {
        "controller": {"frame_port": 9100, "logs_dir": "~/logs"},
        "devices": [],
        "notes": {"owner": "mickey"},
    }
    out = ensure_editor_shape(doc)
    assert out["notes"]["owner"] == "mickey"
    assert out["controller"]["logs_dir"] == "~/logs"


def test_next_device_id_uses_lowest_gap():
    doc = ensure_editor_shape({
        "controller": {"frame_port": 9002},
        "devices": [
            {"device_id": 2},
            {"device_id": 4},
        ],
    })
    assert next_device_id(doc) == 1


def test_make_add_remove_device_entry():
    doc = load_config_doc("/nonexistent/config.json")
    entry = make_device_entry(
        device_uid="sim-1",
        device_type="sim",
        strip_id="main",
        length=60,
        device_id=1,
    )
    add_device(doc, entry)
    assert doc["devices"][0]["host"] == ""
    assert doc["devices"][0]["tcp_port"] == 0
    remove_device(doc, "sim-1")
    assert doc["devices"] == []


def test_remove_missing_device_raises():
    doc = load_config_doc("/nonexistent/config.json")
    with pytest.raises(ConfigError, match="device not found"):
        remove_device(doc, "missing")


def test_save_config_doc_writes_valid_json_and_preserves_unknown_fields(tmp_path):
    path = tmp_path / "config.json"
    doc = {
        "controller": {
            "frame_port": 9002,
            "discovery_port": 6040,
            "logs_dir": "~/logs",
        },
        "devices": [{
            "device_id": 1,
            "device_uid": "sim-1",
            "device_type": "sim",
            "host": "",
            "tcp_port": 0,
            "strip_id": "main",
            "length": 60,
            "label": "left sim",
        }],
        "notes": {"owner": "mickey"},
    }
    save_config_doc(str(path), doc)

    loaded = json.loads(path.read_text())
    assert loaded["notes"]["owner"] == "mickey"
    assert loaded["devices"][0]["label"] == "left sim"

    cfg = load_config(str(path))
    assert cfg.devices[0].device_uid == "sim-1"


def test_save_config_doc_allows_empty_devices(tmp_path):
    path = tmp_path / "config.json"
    doc = {
        "controller": {"frame_port": DEFAULT_FRAME_PORT},
        "devices": [],
    }
    save_config_doc(str(path), doc)
    assert path.exists()

    cfg = load_config(str(path))
    assert cfg.devices == []
