"""Tests for editor-side config helpers (v3 schema)."""

import json

import pytest

from elemctl.config import (
    DEFAULT_DISCOVERY_PORT,
    DEFAULT_FRAME_PORT,
    DEFAULT_LINK_PORT,
    DEFAULT_SYNC_PORT,
    ConfigError,
    load_config,
    load_config_obj,
)
from elemctl.config_edit import (
    add_device,
    edit_device,
    ensure_editor_shape,
    load_config_doc,
    make_device_entry,
    remove_device,
    save_config_doc,
)


def test_load_config_obj_validates_raw_dict():
    cfg = load_config_obj({
        "controller": {"frame_port": 9002},
        "devices": [{
            "device_uid": "sim-1",
            "strip_id": "main",
            "length": 60,
        }],
    })
    assert cfg.frame_port == 9002
    assert cfg.devices[0].device_uid == "sim-1"


def test_load_config_doc_missing_returns_skeleton(tmp_path):
    path = tmp_path / "missing.json"
    doc = load_config_doc(str(path))
    assert doc["controller"]["discovery_port"] == DEFAULT_DISCOVERY_PORT
    assert doc["controller"]["link_port"] == DEFAULT_LINK_PORT
    assert doc["controller"]["frame_port"] == DEFAULT_FRAME_PORT
    assert doc["controller"]["sync_port"] == DEFAULT_SYNC_PORT
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
    assert out["controller"]["frame_port"] == 9100
    assert out["controller"]["link_port"] == DEFAULT_LINK_PORT


def test_make_add_remove_device_entry():
    doc = load_config_doc("/nonexistent/config.json")
    entry = make_device_entry(
        device_uid="sim-1",
        strip_id="main",
        length=60,
    )
    add_device(doc, entry)
    assert doc["devices"][0] == {
        "device_uid": "sim-1",
        "strip_id": "main",
        "length": 60,
    }
    remove_device(doc, "sim-1")
    assert doc["devices"] == []


def test_make_device_entry_with_label():
    entry = make_device_entry(
        device_uid="esp-aabbccddeeff",
        strip_id="porch",
        length=30,
        label="porch rail",
    )
    assert entry["label"] == "porch rail"


def test_edit_device_updates_fields_and_label():
    doc = load_config_doc("/nonexistent/config.json")
    add_device(doc, make_device_entry(
        device_uid="sim-1", strip_id="main", length=60, label="old"))

    edit_device(
        doc, "sim-1",
        device_uid="sim-2", strip_id="left", length=30, label="new",
    )
    assert doc["devices"][0] == {
        "device_uid": "sim-2",
        "strip_id": "left",
        "length": 30,
        "label": "new",
    }

    edit_device(doc, "sim-2", device_uid="sim-2", strip_id="left", length=30)
    assert "label" not in doc["devices"][0]

    with pytest.raises(ConfigError, match="device not found"):
        edit_device(doc, "sim-1", device_uid="sim-3", strip_id="x", length=1)


def test_remove_missing_device_raises():
    doc = load_config_doc("/nonexistent/config.json")
    with pytest.raises(ConfigError, match="device not found"):
        remove_device(doc, "missing")


def test_save_config_doc_writes_valid_json_and_preserves_unknown_fields(tmp_path):
    path = tmp_path / "config.json"
    doc = {
        "controller": {
            "frame_port": 9002,
            "logs_dir": "~/logs",
        },
        "devices": [{
            "device_uid": "sim-1",
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
    assert cfg.devices[0].label == "left sim"


def test_save_config_doc_rejects_invalid_doc(tmp_path):
    path = tmp_path / "config.json"
    doc = {
        "controller": {},
        "devices": [{"device_uid": "bogus", "strip_id": "main", "length": 60}],
    }
    with pytest.raises(ConfigError):
        save_config_doc(str(path), doc)
    assert not path.exists()


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
