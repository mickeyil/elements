"""Launch a local network_sim process from static controller config."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_LOGS_PATH,
    Config,
    ConfigError,
    DeviceConfig,
    load_config,
    resolve_config_path,
    resolve_runtime_path,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BUILD_DIR = REPO_ROOT / 'build'
NETWORK_SIM_BIN = BUILD_DIR / 'network_sim'


def ensure_network_sim_built(*, build_dir: Path = BUILD_DIR) -> None:
    """Build network_sim in an already-configured build tree."""
    if not (build_dir / 'CMakeCache.txt').is_file():
        raise ValueError(
            f'build directory is not configured at {build_dir}; run `cmake -B build` first'
        )

    try:
        subprocess.run(
            ['cmake', '--build', str(build_dir), '--target', 'network_sim'],
            check=True,
            cwd=REPO_ROOT,
        )
    except FileNotFoundError as e:
        raise ValueError('cmake is not available on PATH') from e
    except subprocess.CalledProcessError as e:
        raise ValueError(f'failed to build network_sim in {build_dir}') from e


def build_sim_command(
    config: Config,
    device_uid: str,
    *,
    discovery_host: str = '127.0.0.1',
    tcp_port: int = 0,
    log_file: str | None = None,
    network_sim_bin: Path = NETWORK_SIM_BIN,
) -> list[str]:
    """Build argv for launching network_sim for a known sim device."""
    if not (0 <= tcp_port <= 65535):
        raise ValueError(f'--tcp-port must be 0-65535, got {tcp_port}')

    if config.discovery_port is None:
        raise ValueError('controller.discovery_port is disabled; elemctl sim requires discovery')

    device = _find_device(config, device_uid)

    if not network_sim_bin.is_file():
        raise ValueError(f'network_sim not built at {network_sim_bin}')

    cmd = [
        str(network_sim_bin),
        '--tcp-port', str(tcp_port),
        '--discovery-port', str(config.discovery_port),
        '--discovery-host', discovery_host,
        '--device-uid', device.device_uid,
    ]
    if log_file is not None:
        cmd += ['--log-file', log_file]
    return cmd


def _find_device(config: Config, device_uid: str) -> DeviceConfig:
    for device in config.devices:
        if device.device_uid != device_uid:
            continue
        if device.device_type != 'sim':
            raise ValueError(
                f'device_uid {device_uid!r} is type {device.device_type!r}, not "sim"'
            )
        return device
    raise ValueError(f'unknown sim device_uid: {device_uid!r}')


def main() -> None:
    parser = argparse.ArgumentParser(
        prog='elemctl sim',
        description='Launch network_sim using the static controller config',
    )
    parser.add_argument('device_uid', help='configured sim device UID to launch')
    parser.add_argument(
        '--config', default=DEFAULT_CONFIG_PATH,
        help='config JSON path (default: %(default)s)',
    )
    parser.add_argument(
        '--discovery-host', default='127.0.0.1',
        help='destination host for discovery HELLO packets (default: %(default)s)',
    )
    parser.add_argument(
        '--tcp-port', type=int, default=0,
        help='TCP listen port for the sim, 0 means ephemeral (default: %(default)s)',
    )
    parser.add_argument(
        '--log-dir', default=None,
        help='logs directory (default: controller.logs_dir or <repo>/logs)',
    )
    args = parser.parse_args()

    try:
        config_path = resolve_config_path(args.config)
        config = load_config(config_path)
        log_dir = resolve_runtime_path(args.log_dir, config.logs_dir, DEFAULT_LOGS_PATH)
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        ensure_network_sim_built()
        cmd = build_sim_command(
            config,
            args.device_uid,
            discovery_host=args.discovery_host,
            tcp_port=args.tcp_port,
            log_file=str(Path(log_dir) / f'{args.device_uid}.log'),
        )
    except (ConfigError, json.JSONDecodeError, ValueError) as e:
        print(f'elemctl sim: {e}', file=sys.stderr)
        sys.exit(1)

    try:
        os.execv(cmd[0], cmd)
    except OSError as e:
        print(f'elemctl sim: failed to exec {cmd[0]}: {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
