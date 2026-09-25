"""Launch and supervise a sim device process.

The supervisor stays in the foreground and owns one child: the
sim_device binary. It restarts the child when it exits with the reboot
sentinel (the sim's reboot path is a real process exit, so RAM is
truly wiped and boot_token regenerates on the way back up) and stops
on any other exit. A crash-loop guard gives up when reboots come too
fast. Ctrl-C reaches the whole foreground process group, so the sim
shuts itself down and the supervisor sees a normal exit; SIGTERM sent
to the supervisor alone is forwarded to the child.

The device argument is a sim uid or a strip id: a strip id resolves, via
the config, to the one configured sim device serving that strip, so the
uid of a sim twin stays infrastructure the user never has to type.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_FRAME_PORT,
    Config,
    ConfigError,
    load_config,
    validate_device_uid,
)
from .procs import child_env

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BUILD_DIR = REPO_ROOT / 'build'
SIM_DEVICE_BIN = BUILD_DIR / 'sim_device'

# Mirrors SIM_REBOOT_EXIT_CODE in src/platform/sim/sim_system_platform.h; Python
# cannot read the C++ constant.
SIM_REBOOT_EXIT_CODE = 64

# Crash-loop guard: give up when this many reboots land within the window.
MAX_REBOOTS = 5
REBOOT_WINDOW_SEC = 30.0

DEFAULT_CONTROLLER_HOST = '127.0.0.1'

STORAGE_ROOT_ENV = 'ELEMENTS_SIM_STORAGE_ROOT'


def validate_sim_uid(uid: str) -> str | None:
    """Check uid against the wire policy (config.validate_device_uid),
    requiring a sim uid.

    Returns an error message, or None when the uid is acceptable.
    """
    if uid and not uid.startswith('sim-'):
        return f'sim uid must start with "sim-": {uid!r}'
    return validate_device_uid(uid)


def resolve_sim_uid(device: str, config: Config) -> str:
    """Resolve the `elemctl sim` argument to a sim uid.

    A configured device uid is taken as is. Otherwise the argument names a
    strip, and the one configured sim device serving it is the answer. An
    unconfigured sim- uid that names no strip still runs as itself, so a
    sim can come up first and be configured from its discovered stub.
    Raises ValueError when no sim device, or more than one, serves it.
    """
    if any(dc.device_uid == device for dc in config.devices):
        return device
    sims = [dc.device_uid for dc in config.devices
            if dc.device_type == 'sim' and dc.strip_id == device]
    if len(sims) == 1:
        return sims[0]
    if not sims and device.startswith('sim-'):
        return device
    if not sims:
        raise ValueError(
            f'no configured device uid or sim device serves strip {device!r}; '
            'create one with Simulate on a device card in the web status page, '
            'or add a sim- device to the config'
        )
    raise ValueError(
        f'strip {device!r} is served by several sim devices '
        f'({", ".join(sims)}); pass the device uid instead'
    )


def build_sim_command(
    device_uid: str,
    *,
    controller_host: str = DEFAULT_CONTROLLER_HOST,
    frame_port: int = DEFAULT_FRAME_PORT,
    sim_device_bin: Path = SIM_DEVICE_BIN,
) -> list[str]:
    """Build the sim_device argv. The same list is reused for every re-exec."""
    if not (1 <= frame_port <= 65535):
        raise ValueError(f'--frame-port must be 1-65535, got {frame_port}')
    if not sim_device_bin.is_file():
        raise ValueError(
            f"sim_device not built at {sim_device_bin}; run './elemctl setup'"
        )

    return [
        str(sim_device_bin),
        '--device-uid', device_uid,
        '--controller-host', controller_host,
        '--frame-port', str(frame_port),
    ]


def supervise(
    cmd: list[str],
    env: dict[str, str] | None = None,
    *,
    max_reboots: int = MAX_REBOOTS,
    reboot_window_sec: float = REBOOT_WINDOW_SEC,
    clock=time.monotonic,
) -> int:
    """Run cmd, re-running it whenever it exits with the reboot sentinel.

    Returns the exit code to surface: the child's last code, or 1 when
    the crash-loop guard trips. Installs a SIGTERM handler for the
    duration, so it must run on the main thread.
    """
    reboot_times: collections.deque[float] = collections.deque(maxlen=max_reboots)
    state = {'proc': None, 'stop': False}

    def _on_sigterm(signum, frame):
        state['stop'] = True
        if state['proc'] is not None:
            state['proc'].terminate()

    prev_handler = signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        while True:
            proc = subprocess.Popen(cmd, env=env)
            state['proc'] = proc
            try:
                code = proc.wait()
            except KeyboardInterrupt:
                # Ctrl-C reached the whole foreground group; the sim
                # handles SIGINT itself and exits.
                code = proc.wait()
                return code
            finally:
                state['proc'] = None

            if state['stop'] or code != SIM_REBOOT_EXIT_CODE:
                return code

            now = clock()
            reboot_times.append(now)
            if (len(reboot_times) == max_reboots
                    and now - reboot_times[0] <= reboot_window_sec):
                print(
                    f'elemctl sim: {max_reboots} reboots within '
                    f'{reboot_window_sec:.0f}s; giving up',
                    file=sys.stderr,
                )
                return 1
    finally:
        signal.signal(signal.SIGTERM, prev_handler)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog='elemctl sim',
        description='Launch and supervise a sim device',
    )
    parser.add_argument(
        'device',
        help='strip id served by a configured sim device, or a sim device UID '
             '(sim-..., max 16 chars)',
    )
    parser.add_argument(
        '--controller-host', default=DEFAULT_CONTROLLER_HOST,
        help='controller IPv4 for unicast discovery (default: %(default)s)',
    )
    parser.add_argument(
        '--frame-port', type=int, default=None,
        help='controller frame-preview UDP port (default: the config\'s '
             f'controller.frame_port, else {DEFAULT_FRAME_PORT})',
    )
    parser.add_argument(
        '--storage-root', default=str(REPO_ROOT / 'local'),
        help=f'device storage root, exported as {STORAGE_ROOT_ENV} '
             '(default: %(default)s)',
    )
    args = parser.parse_args()

    # Without a readable config the argument can only be a uid.
    try:
        config: Config | None = load_config(DEFAULT_CONFIG_PATH)
    except (OSError, ConfigError, json.JSONDecodeError):
        config = None

    device_uid = args.device
    if config is not None:
        try:
            device_uid = resolve_sim_uid(args.device, config)
        except ValueError as e:
            print(f'elemctl sim: {e}', file=sys.stderr)
            sys.exit(2)

    error = validate_sim_uid(device_uid)
    if error is not None:
        print(f'elemctl sim: {error}', file=sys.stderr)
        sys.exit(2)

    frame_port = args.frame_port
    if frame_port is None:
        frame_port = config.frame_port if config is not None else DEFAULT_FRAME_PORT

    try:
        cmd = build_sim_command(
            device_uid,
            controller_host=args.controller_host,
            frame_port=frame_port,
        )
    except ValueError as e:
        print(f'elemctl sim: {e}', file=sys.stderr)
        sys.exit(1)

    # We are the sim's parent; if we die hard, the kernel stops the sim.
    env = child_env()
    env[STORAGE_ROOT_ENV] = args.storage_root

    code = supervise(cmd, env)
    if code < 0:
        print(f'elemctl sim: sim_device died on signal {-code}', file=sys.stderr)
        sys.exit(128 - code)
    sys.exit(code)


if __name__ == '__main__':
    main()
