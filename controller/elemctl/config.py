"""Static config loading for elemctl.

Reads a JSON config describing the controller and device topology.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

DEFAULT_CONFIG_PATH = '~/.config/elemctl/config.json'
DEFAULT_SOCKET_PATH = '/tmp/elemctl.sock'

_VALID_DEVICE_TYPES = {"sim", "esp32"}


def _is_int(val) -> bool:
    """True for int, False for bool (bool is a subclass of int in Python)."""
    return isinstance(val, int) and not isinstance(val, bool)


class ConfigError(ValueError):
    """Raised for invalid or missing config."""


def resolve_config_path(path: str) -> str:
    """Expand ~ and verify the config file exists. Raises ConfigError if missing."""
    resolved = os.path.expanduser(path)
    if not os.path.isfile(resolved):
        raise ConfigError(
            f"config file not found: {resolved}\n"
            f"create your config at {DEFAULT_CONFIG_PATH} or pass --config"
        )
    return resolved


@dataclass
class DeviceConfig:
    device_id: int      # u16, wire protocol ID
    device_uid: str     # unique hardware identifier (used by discovery)
    device_type: str    # "sim" or "esp32"
    host: str           # IP address
    tcp_port: int       # TCP listen port
    strip_id: str       # logical name (matches DSL)
    length: int         # pixel count


@dataclass
class Config:
    frame_port: int             # UDP port for frame receipt
    devices: list[DeviceConfig]
    discovery_port: int | None = None  # UDP port for HELLO packets


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

    discovery_port = ctrl.get("discovery_port")
    if discovery_port is not None:
        if not _is_int(discovery_port):
            raise ConfigError("'controller.discovery_port' must be an integer")
        if not (1 <= discovery_port <= 65535):
            raise ConfigError(
                f"'controller.discovery_port' must be 1-65535, got {discovery_port}"
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
    seen_uids: set[str] = set()
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

        if d["device_uid"] == "":
            raise ConfigError(
                f"devices[{i}].device_uid must be non-empty"
            )

        # When discovery is enabled, host="" and tcp_port=0 are valid sentinels
        # meaning "awaiting discovery". Otherwise require real values.
        _awaiting = discovery_port is not None and d["host"] == "" and d["tcp_port"] == 0
        if not _awaiting:
            if d["host"] == "" and discovery_port is not None:
                raise ConfigError(
                    f"devices[{i}]: host and tcp_port must both be empty "
                    f"or both set when discovery is enabled"
                )
            if not (1 <= d["tcp_port"] <= 65535):
                raise ConfigError(
                    f"devices[{i}].tcp_port must be 1-65535, got {d['tcp_port']}"
                )
            if d["host"] == "":
                raise ConfigError(
                    f"devices[{i}].host must be non-empty"
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

        if d["device_uid"] in seen_uids:
            raise ConfigError(f"duplicate device_uid: {d['device_uid']!r}")
        seen_uids.add(d["device_uid"])

        if d["strip_id"] in seen_strip_ids:
            raise ConfigError(f"duplicate strip_id: {d['strip_id']!r}")
        seen_strip_ids.add(d["strip_id"])

        endpoint = (d["host"], d["tcp_port"])
        if not _awaiting:
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

    return Config(frame_port=frame_port, devices=devices, discovery_port=discovery_port)
