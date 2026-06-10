"""Launch and supervise a sim device process.

The supervisor stays in the foreground and owns one child: the
sim_device binary. It restarts the child when it exits with the reboot
sentinel (the sim's reboot path is a real process exit, so RAM is
truly wiped and boot_token regenerates on the way back up) and stops
on any other exit. A crash-loop guard gives up when reboots come too
fast. Ctrl-C reaches the whole foreground process group, so the sim
shuts itself down and the supervisor sees a normal exit; SIGTERM sent
to the supervisor alone is forwarded to the child.
"""

from __future__ import annotations

import argparse
import collections
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BUILD_DIR = REPO_ROOT / 'build'
SIM_DEVICE_BIN = BUILD_DIR / 'sim_device'

# Mirrors SIM_REBOOT_EXIT_CODE in src/sim/sim_system_platform.h; Python
# cannot read the C++ constant.
SIM_REBOOT_EXIT_CODE = 64

# Crash-loop guard: give up when this many reboots land within the window.
MAX_REBOOTS = 5
REBOOT_WINDOW_SEC = 30.0

DEFAULT_CONTROLLER_HOST = '127.0.0.1'
DEFAULT_FRAME_PORT = 6042

# Wire slot size; see src/device_identity.h.
UID_MAX_BYTES = 16

STORAGE_ROOT_ENV = 'ELEMENTS_SIM_STORAGE_ROOT'


def validate_sim_uid(uid: str) -> str | None:
    """Check uid against the wire policy (controller_link.md § Identity).

    Returns an error message, or None when the uid is acceptable.
    """
    if not uid:
        return 'device uid is empty'
    if not uid.startswith('sim-'):
        return f'sim uid must start with "sim-": {uid!r}'
    if len(uid) > UID_MAX_BYTES:
        return f'sim uid longer than {UID_MAX_BYTES} bytes: {uid!r}'
    if not all(0x20 <= ord(c) <= 0x7E for c in uid):
        return f'sim uid must be printable ASCII: {uid!r}'
    return None


def ensure_sim_device_built(*, build_dir: Path = BUILD_DIR) -> None:
    """Build sim_device in an already-configured build tree."""
    if not (build_dir / 'CMakeCache.txt').is_file():
        raise ValueError(
            f'build directory is not configured at {build_dir}; run `cmake -B build` first'
        )

    try:
        subprocess.run(
            ['cmake', '--build', str(build_dir), '--target', 'sim_device'],
            check=True,
            cwd=REPO_ROOT,
        )
    except FileNotFoundError as e:
        raise ValueError('cmake is not available on PATH') from e
    except subprocess.CalledProcessError as e:
        raise ValueError(f'failed to build sim_device in {build_dir}') from e


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
        raise ValueError(f'sim_device not built at {sim_device_bin}')

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
    parser.add_argument('device_uid', help='sim device UID (sim-..., max 16 chars)')
    parser.add_argument(
        '--controller-host', default=DEFAULT_CONTROLLER_HOST,
        help='controller IPv4 for unicast discovery (default: %(default)s)',
    )
    parser.add_argument(
        '--frame-port', type=int, default=DEFAULT_FRAME_PORT,
        help='controller frame-preview UDP port (default: %(default)s)',
    )
    parser.add_argument(
        '--storage-root', default=str(REPO_ROOT / 'local'),
        help=f'device storage root, exported as {STORAGE_ROOT_ENV} '
             '(default: %(default)s)',
    )
    args = parser.parse_args()

    error = validate_sim_uid(args.device_uid)
    if error is not None:
        print(f'elemctl sim: {error}', file=sys.stderr)
        sys.exit(2)

    try:
        ensure_sim_device_built()
        cmd = build_sim_command(
            args.device_uid,
            controller_host=args.controller_host,
            frame_port=args.frame_port,
        )
    except ValueError as e:
        print(f'elemctl sim: {e}', file=sys.stderr)
        sys.exit(1)

    env = os.environ.copy()
    env[STORAGE_ROOT_ENV] = args.storage_root

    code = supervise(cmd, env)
    if code < 0:
        print(f'elemctl sim: sim_device died on signal {-code}', file=sys.stderr)
        sys.exit(128 - code)
    sys.exit(code)


if __name__ == '__main__':
    main()
