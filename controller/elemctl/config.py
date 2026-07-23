"""Static config loading for elemctl.

Reads a JSON config describing the controller and device topology.

v3 shape: the controller section holds the five well-known ports; a
device entry is just
`{device_uid, strip_id, length, label?}`. Devices connect in and are
identified by UID alone, so the config stores no addresses and no
numeric ids. The device type is implied by the UID prefix
(src/platform/device_identity.h).
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTANCE_DIR = REPO_ROOT / 'instance'
DEFAULT_CONFIG_PATH = str(INSTANCE_DIR / 'config.json')
DEFAULT_SOCKET_PATH = '/tmp/elemctl.sock'

DEFAULT_DISCOVERY_PORT = 6040
DEFAULT_LINK_PORT = 6041
DEFAULT_FRAME_PORT = 6042
DEFAULT_SYNC_PORT = 6043
DEFAULT_LOG_PORT = 6044

# Must match MAX_STRIP_PIXELS in src/core/hardware_profile.h
MAX_DEVICE_PIXELS = 300
# Must match UID_SIZE in src/platform/device_identity.h (wire slot).
MAX_UID_BYTES = 16

DEFAULT_ANIMATIONS_PATH = str(REPO_ROOT / 'animations')
DEFAULT_LOGS_PATH = str(REPO_ROOT / 'logs')

_PORT_FIELDS = ('discovery_port', 'link_port', 'frame_port', 'sync_port',
                'log_port')
_ESP_UID_RE = re.compile(r'^esp-[0-9a-f]{12}$')
_STRIP_ID_RE = re.compile(r'^[A-Za-z0-9_-]+$')


def _is_int(val) -> bool:
    """True for int, False for bool (bool is a subclass of int in Python)."""
    return isinstance(val, int) and not isinstance(val, bool)


class ConfigError(ValueError):
    """Raised for invalid or missing config."""


def validate_device_uid(device_uid: str) -> str | None:
    """Check a UID against the wire policy (src/platform/device_identity.h).

    Returns an error message, or None when the uid is acceptable.
    """
    if not isinstance(device_uid, str) or not device_uid:
        return 'device_uid must be a non-empty string'
    if len(device_uid.encode('utf-8', errors='replace')) > MAX_UID_BYTES:
        return f'device_uid longer than {MAX_UID_BYTES} bytes: {device_uid!r}'
    if not all(0x20 <= ord(c) <= 0x7E for c in device_uid):
        return f'device_uid must be printable ASCII: {device_uid!r}'
    if device_uid.startswith('sim-'):
        if len(device_uid) == len('sim-'):
            return f'sim uid must continue after "sim-": {device_uid!r}'
        return None
    if device_uid.startswith('esp-'):
        if not _ESP_UID_RE.fullmatch(device_uid):
            return f'esp uid must be esp-<12 lowercase hex>: {device_uid!r}'
        return None
    return f'device_uid must start with "sim-" or "esp-": {device_uid!r}'


def default_config_doc() -> dict:
    """Return the default first-run config document."""
    return {
        "controller": {
            "discovery_port": DEFAULT_DISCOVERY_PORT,
            "link_port": DEFAULT_LINK_PORT,
            "frame_port": DEFAULT_FRAME_PORT,
            "sync_port": DEFAULT_SYNC_PORT,
            "log_port": DEFAULT_LOG_PORT,
        },
        "devices": [],
    }


def write_json_file_atomic(path: Path, doc: dict) -> None:
    """Write JSON atomically with a trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        prefix=f'.{path.name}.tmp-',
        suffix='.json',
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(doc, f, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def create_default_config(path: str) -> str:
    """Create a fresh default config file and return its resolved path."""
    resolved_path = Path(os.path.expanduser(path)).resolve()
    write_json_file_atomic(resolved_path, default_config_doc())
    log.warning('created default config at %s', resolved_path)
    return str(resolved_path)


def resolve_config_path(path: str | None = None) -> str:
    """Resolve config path, auto-creating the default config when needed.

    When path is None, the repo-local default config path is used and the file
    is created on first run if it does not exist.

    When path is explicit, the file must already exist.
    """
    if path is None:
        resolved = Path(DEFAULT_CONFIG_PATH).resolve()
        if not resolved.is_file():
            return create_default_config(str(resolved))
        return str(resolved)

    resolved = Path(os.path.expanduser(path)).resolve()
    if not resolved.is_file():
        raise ConfigError(
            f"config file not found: {resolved}\n"
            f"omit --config to create the default config at {DEFAULT_CONFIG_PATH}"
        )
    return str(resolved)


@dataclass
class DeviceConfig:
    device_uid: str        # the only device identifier (wire UID)
    strip_id: str          # logical name the DSL routes by
    length: int            # pixel count
    label: str | None = None  # optional UI display string

    @property
    def device_type(self) -> str:
        """'sim' or 'esp32', implied by the UID prefix."""
        return 'sim' if self.device_uid.startswith('sim-') else 'esp32'


@dataclass
class Config:
    discovery_port: int
    link_port: int
    frame_port: int
    sync_port: int
    log_port: int
    devices: list[DeviceConfig]
    animations_dir: str | None = None  # override for animations directory
    logs_dir: str | None = None        # override for logs directory


def resolve_runtime_path(
    cli_override: str | None,
    config_value: str | None,
    default: str,
) -> str:
    """Resolve a runtime path with CLI > config > default precedence."""
    return os.path.expanduser(cli_override or config_value or default)


def load_config(path: str) -> Config:
    """Read JSON config, validate, return Config. Raises ConfigError."""
    with open(path) as f:
        raw = json.load(f)

    return load_config_obj(raw)


def load_config_obj(raw: dict) -> Config:
    """Validate a raw config object and return Config."""
    if not isinstance(raw, dict):
        raise ConfigError("config must be a JSON object")

    # --- controller section ---
    ctrl = raw.get("controller")
    if ctrl is None:
        raise ConfigError("missing 'controller' section")
    if not isinstance(ctrl, dict):
        raise ConfigError("'controller' must be an object")

    defaults = default_config_doc()["controller"]
    ports: dict[str, int] = {}
    for field in _PORT_FIELDS:
        if field not in ctrl:
            ports[field] = defaults[field]
            continue
        value = ctrl[field]
        if not _is_int(value):
            raise ConfigError(f"'controller.{field}' must be an integer")
        if not (1 <= value <= 65535):
            raise ConfigError(f"'controller.{field}' must be 1-65535, got {value}")
        ports[field] = value

    seen_ports: dict[int, str] = {}
    for field in _PORT_FIELDS:
        previous = seen_ports.get(ports[field])
        if previous is not None:
            raise ConfigError(
                f"'controller.{field}' duplicates 'controller.{previous}': "
                f"{ports[field]}"
            )
        seen_ports[ports[field]] = field

    animations_dir = ctrl.get("animations_dir")
    if animations_dir is not None:
        if not isinstance(animations_dir, str):
            raise ConfigError("'controller.animations_dir' must be a string")

    logs_dir = ctrl.get("logs_dir")
    if logs_dir is not None:
        if not isinstance(logs_dir, str):
            raise ConfigError("'controller.logs_dir' must be a string")

    # --- devices section ---
    devices_raw = raw.get("devices")
    if devices_raw is None:
        raise ConfigError("missing 'devices' section")
    if not isinstance(devices_raw, list):
        raise ConfigError("'devices' must be a list")

    devices: list[DeviceConfig] = []
    seen_uids: set[str] = set()
    strip_lengths_by_id: dict[str, int] = {}

    for i, d in enumerate(devices_raw):
        if not isinstance(d, dict):
            raise ConfigError(f"devices[{i}] must be an object")

        device_uid = d.get("device_uid")
        uid_error = validate_device_uid(device_uid)
        if uid_error is not None:
            raise ConfigError(f"devices[{i}]: {uid_error}")

        strip_id = d.get("strip_id")
        if not isinstance(strip_id, str) or not strip_id:
            raise ConfigError(f"devices[{i}].strip_id must be a non-empty string")
        if not _STRIP_ID_RE.fullmatch(strip_id):
            raise ConfigError(
                f"devices[{i}].strip_id may only contain letters, numbers, "
                f"_ and -, got {strip_id!r}"
            )

        length = d.get("length")
        if not _is_int(length):
            raise ConfigError(f"devices[{i}].length must be an integer")
        if not (1 <= length <= MAX_DEVICE_PIXELS):
            raise ConfigError(
                f"devices[{i}].length must be 1-{MAX_DEVICE_PIXELS}, got {length}"
            )

        label = d.get("label")
        if label is not None and (not isinstance(label, str) or not label):
            raise ConfigError(f"devices[{i}].label must be a non-empty string")

        if device_uid in seen_uids:
            raise ConfigError(f"duplicate device_uid: {device_uid!r}")
        seen_uids.add(device_uid)

        existing_length = strip_lengths_by_id.get(strip_id)
        if existing_length is None:
            strip_lengths_by_id[strip_id] = length
        elif existing_length != length:
            raise ConfigError(
                f"duplicate strip_id with different length: {strip_id!r}"
            )

        devices.append(DeviceConfig(
            device_uid=device_uid,
            strip_id=strip_id,
            length=length,
            label=label,
        ))

    return Config(
        discovery_port=ports["discovery_port"],
        link_port=ports["link_port"],
        frame_port=ports["frame_port"],
        sync_port=ports["sync_port"],
        log_port=ports["log_port"],
        devices=devices,
        animations_dir=animations_dir,
        logs_dir=logs_dir,
    )
