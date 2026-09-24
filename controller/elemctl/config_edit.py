"""Helpers for editing elemctl config docs while preserving unknown fields."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .config import (
    ConfigError,
    load_config_obj,
    default_config_doc,
    write_json_file_atomic,
)


def ensure_editor_shape(doc: dict) -> dict:
    """Ensure the config doc has the minimum editable shape.

    Mutates and returns the raw dict, preserving unknown fields.
    """
    if not isinstance(doc, dict):
        raise ConfigError("config must be a JSON object")

    defaults = default_config_doc()

    ctrl = doc.get("controller")
    if ctrl is None:
        ctrl = {}
        doc["controller"] = ctrl
    if not isinstance(ctrl, dict):
        raise ConfigError("'controller' must be an object")
    for field, value in defaults["controller"].items():
        ctrl.setdefault(field, value)

    devices = doc.get("devices")
    if devices is None:
        devices = list(defaults["devices"])
        doc["devices"] = devices
    if not isinstance(devices, list):
        raise ConfigError("'devices' must be a list")

    return doc


def load_config_doc(path: str) -> dict:
    """Load an editable config doc, or return a skeleton if missing."""
    resolved = os.path.expanduser(path)
    if not os.path.exists(resolved):
        return ensure_editor_shape({})

    with open(resolved) as f:
        raw = json.load(f)
    return ensure_editor_shape(raw)


def make_device_entry(
    *,
    device_uid: str,
    strip_id: str,
    length: int,
    label: str | None = None,
) -> dict:
    """Build a device entry for the config doc."""
    entry = {
        "device_uid": device_uid,
        "strip_id": strip_id,
        "length": length,
    }
    if label is not None:
        entry["label"] = label
    return entry


def add_device(doc: dict, entry: dict) -> None:
    """Append a device entry to the raw config doc."""
    ensure_editor_shape(doc)["devices"].append(entry)


def remove_device(doc: dict, device_uid: str) -> None:
    """Remove a device by uid. Raises ConfigError if missing."""
    devices = ensure_editor_shape(doc)["devices"]
    for idx, device in enumerate(devices):
        if isinstance(device, dict) and device.get("device_uid") == device_uid:
            del devices[idx]
            return
    raise ConfigError(f"device not found: {device_uid}")


def edit_device(
    doc: dict,
    target_device_uid: str,
    *,
    device_uid: str,
    strip_id: str,
    length: int,
    label: str | None = None,
) -> list[str]:
    """Edit a device in place. Raises ConfigError if target is missing.

    strip_id and length belong to the strip group: every other device that
    shares the target's old strip_id takes the new values too, since config
    allows one length per strip and a mirrored member could never be edited
    alone. device_uid and label stay per-device.

    Returns the uids of the other group members whose entry changed.
    """
    devices = ensure_editor_shape(doc)["devices"]
    target = next(
        (d for d in devices
         if isinstance(d, dict) and d.get("device_uid") == target_device_uid),
        None,
    )
    if target is None:
        raise ConfigError(f"device not found: {target_device_uid}")

    changed: list[str] = []
    old_strip_id = target.get("strip_id")
    for device in devices:
        if (device is target or not isinstance(device, dict)
                or device.get("strip_id") != old_strip_id):
            continue
        if device.get("strip_id") != strip_id or device.get("length") != length:
            device["strip_id"] = strip_id
            device["length"] = length
            changed.append(device.get("device_uid"))

    target["device_uid"] = device_uid
    target["strip_id"] = strip_id
    target["length"] = length
    if label is None:
        target.pop("label", None)
    else:
        target["label"] = label
    return changed


def save_config_doc(path: str, doc: dict) -> None:
    """Validate and atomically save the config doc."""
    load_config_obj(doc)

    resolved = Path(os.path.expanduser(path))
    write_json_file_atomic(resolved, doc)
