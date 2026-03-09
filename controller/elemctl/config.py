"""Static config loading for elemctl.

Reads a JSON config describing the controller and device topology.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

_VALID_DEVICE_TYPES = {"sim", "esp32"}


def _is_int(val) -> bool:
    """True for int, False for bool (bool is a subclass of int in Python)."""
    return isinstance(val, int) and not isinstance(val, bool)


class ConfigError(ValueError):
    """Raised for invalid or missing config."""


@dataclass
class DeviceConfig:
    device_id: int      # u16, wire protocol ID
    device_uid: str     # unique hardware identifier (unused in v1)
    device_type: str    # "sim" or "esp32"
    host: str           # IP address
    tcp_port: int       # TCP listen port
    strip_id: str       # logical name (matches DSL)
    length: int         # pixel count


@dataclass
class Config:
    frame_port: int             # UDP port for frame receipt
    devices: list[DeviceConfig]


def load_config(path: str) -> Config:
    """Read JSON config, validate, return Config. Raises ConfigError."""
    with open(path) as f:
        raw = json.load(f)

    if not isinstance(raw, dict):
        raise ConfigError("config must be a JSON object")

    # --- controller section ---
    ctrl = raw.get("controller")
    if ctrl is None:
        raise ConfigError("missing 'controller' section")
    if not isinstance(ctrl, dict):
        raise ConfigError("'controller' must be an object")

    frame_port = ctrl.get("frame_port")
    if frame_port is None:
        raise ConfigError("missing 'controller.frame_port'")
    if not _is_int(frame_port):
        raise ConfigError("'controller.frame_port' must be an integer")
    if not (1 <= frame_port <= 65535):
        raise ConfigError(
            f"'controller.frame_port' must be 1-65535, got {frame_port}"
        )

    # --- devices section ---
    devices_raw = raw.get("devices")
    if devices_raw is None:
        raise ConfigError("missing 'devices' section")
    if not isinstance(devices_raw, list) or len(devices_raw) == 0:
        raise ConfigError("'devices' must be a non-empty list")

    _DEVICE_FIELDS = {
        "device_id": int,
        "device_uid": str,
        "device_type": str,
        "host": str,
        "tcp_port": int,
        "strip_id": str,
        "length": int,
    }

    devices: list[DeviceConfig] = []
    seen_ids: set[int] = set()
    seen_strip_ids: set[str] = set()
    seen_endpoints: set[tuple[str, int]] = set()

    for i, d in enumerate(devices_raw):
        if not isinstance(d, dict):
            raise ConfigError(f"devices[{i}] must be an object")

        for field, typ in _DEVICE_FIELDS.items():
            val = d.get(field)
            if val is None:
                raise ConfigError(f"devices[{i}] missing '{field}'")
            if typ is int:
                if not _is_int(val):
                    raise ConfigError(
                        f"devices[{i}].{field} must be {typ.__name__}"
                    )
            elif not isinstance(val, typ):
                raise ConfigError(
                    f"devices[{i}].{field} must be {typ.__name__}"
                )

        if not (0 <= d["device_id"] <= 0xFFFF):
            raise ConfigError(
                f"devices[{i}].device_id must be 0-65535, got {d['device_id']}"
            )
        if not (1 <= d["tcp_port"] <= 65535):
            raise ConfigError(
                f"devices[{i}].tcp_port must be 1-65535, got {d['tcp_port']}"
            )
        if d["length"] < 1:
            raise ConfigError(
                f"devices[{i}].length must be >= 1, got {d['length']}"
            )

        if d["device_type"] not in _VALID_DEVICE_TYPES:
            raise ConfigError(
                f"devices[{i}].device_type must be one of {_VALID_DEVICE_TYPES}, "
                f"got {d['device_type']!r}"
            )

        if d["device_id"] in seen_ids:
            raise ConfigError(f"duplicate device_id: {d['device_id']}")
        seen_ids.add(d["device_id"])

        if d["strip_id"] in seen_strip_ids:
            raise ConfigError(f"duplicate strip_id: {d['strip_id']!r}")
        seen_strip_ids.add(d["strip_id"])

        endpoint = (d["host"], d["tcp_port"])
        if endpoint in seen_endpoints:
            raise ConfigError(
                f"duplicate endpoint: {d['host']}:{d['tcp_port']}"
            )
        seen_endpoints.add(endpoint)

        devices.append(DeviceConfig(
            device_id=d["device_id"],
            device_uid=d["device_uid"],
            device_type=d["device_type"],
            host=d["host"],
            tcp_port=d["tcp_port"],
            strip_id=d["strip_id"],
            length=d["length"],
        ))

    return Config(frame_port=frame_port, devices=devices)
