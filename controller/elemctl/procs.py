"""Tie a child process's lifetime to the process that spawned it.

A supervisor (the `elemctl run` launcher, `elemctl sim`) stops its children
on ctrl-c or SIGTERM, but a supervisor that is SIGKILLed or crashes cannot,
and its children would keep running unowned (holding the socket, the HTTP
port, a sim uid). So the spawner puts its pid in ELEMCTL_PARENT_PID
(child_env), and the child asks the kernel for SIGTERM when its parent dies
(exit_with_parent), which runs its normal shutdown path. The sim_device
binary does the same in C++ (src/platform/sim/main.cpp).

Linux only: PR_SET_PDEATHSIG is a Linux prctl; elsewhere this is a no-op.
The signal fires when the spawning *thread* exits, so spawn from a thread
that lives as long as the parent does (the main thread, in practice).
"""

from __future__ import annotations

import ctypes
import os
import signal
import sys

PARENT_PID_ENV = 'ELEMCTL_PARENT_PID'

_PR_SET_PDEATHSIG = 1


def child_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """A copy of env (default: ours) naming this process as the child's parent."""
    env = dict(os.environ if env is None else env)
    env[PARENT_PID_ENV] = str(os.getpid())
    return env


def exit_with_parent() -> None:
    """Get SIGTERM when the spawning parent dies; exit now if it already has.

    Does nothing unless a supervisor set ELEMCTL_PARENT_PID, so a service
    started by hand from a shell is unaffected.
    """
    parent = os.environ.get(PARENT_PID_ENV)
    if not parent or not sys.platform.startswith('linux'):
        return
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)
    # The parent may have died before we armed; we were reparented then.
    if os.getppid() != int(parent):
        print(f'elemctl: parent {parent} is gone; exiting', file=sys.stderr)
        sys.exit(0)
