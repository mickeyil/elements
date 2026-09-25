"""Run sim device processes on behalf of the web UI.

The web server owns these: Power on a sim card starts its sim_device, Power
off stops it, and the web's shutdown stops them all. Each sim runs in its own
task that supervises it the way `elemctl sim` does: an exit with the reboot
sentinel is the sim rebooting, so it is respawned (behind the same crash-loop
guard); any other exit leaves it off, with the reason kept for the page unless
it was a clean exit 0.

One process per uid, ever: the controller replaces a device's link when a
second one with the same uid connects, so two would fight over it.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import DEFAULT_FRAME_PORT
from .procs import child_env
from .sim import (
    MAX_REBOOTS,
    REBOOT_WINDOW_SEC,
    REPO_ROOT,
    SIM_DEVICE_BIN,
    SIM_REBOOT_EXIT_CODE,
    STORAGE_ROOT_ENV,
    RebootGuard,
    build_sim_command,
)

log = logging.getLogger(__name__)

# How long a stopping sim gets to shut down after SIGTERM before SIGKILL.
STOP_TIMEOUT_SEC = 5.0


@dataclass(eq=False)
class _Sim:
    running: bool = True
    pid: int | None = None
    last_exit: str | None = None
    stopping: bool = False
    proc: asyncio.subprocess.Process | None = None
    task: asyncio.Task | None = None


def _describe_exit(code: int) -> str:
    if code < 0:
        try:
            return f'killed by {signal.Signals(-code).name}'
        except ValueError:
            return f'killed by signal {-code}'
    return f'exited with code {code}'


class SimManager:
    """The web's sims, by uid. on_change is called on the loop whenever
    state() changes."""

    def __init__(
        self,
        on_change: Callable[[], None] = lambda: None,
        *,
        frame_port: int = DEFAULT_FRAME_PORT,
        storage_root: Path = REPO_ROOT / 'local',
        sim_device_bin: Path = SIM_DEVICE_BIN,
    ):
        self.on_change = on_change
        self._frame_port = frame_port
        self._storage_root = storage_root
        self._sim_device_bin = sim_device_bin
        self._sims: dict[str, _Sim] = {}

    def state(self) -> dict[str, dict]:
        return {
            uid: {'running': sim.running, 'pid': sim.pid, 'last_exit': sim.last_exit}
            for uid, sim in self._sims.items()
        }

    def is_running(self, uid: str) -> bool:
        sim = self._sims.get(uid)
        return sim is not None and sim.running

    def start(self, uid: str) -> None:
        """Start uid's sim; ValueError when it already runs or is not built."""
        if self.is_running(uid):
            raise ValueError(f'{uid} is already running')
        cmd = build_sim_command(
            uid, frame_port=self._frame_port, sim_device_bin=self._sim_device_bin)
        # Registered before anything awaits, so a second start sees it running.
        sim = _Sim()
        self._sims[uid] = sim
        sim.task = asyncio.create_task(self._run(uid, sim, cmd))
        self.on_change()

    def stop(self, uid: str) -> None:
        """SIGTERM uid's sim, SIGKILL if it outlives STOP_TIMEOUT_SEC; its task
        records the exit. ValueError when it is not running."""
        sim = self._sims.get(uid)
        if sim is None or not sim.running:
            raise ValueError(f'{uid} is not running')
        if sim.stopping:
            return
        sim.stopping = True
        # A sim still being spawned is terminated by its task once it exists.
        if sim.proc is not None:
            self._terminate(sim.proc)

    async def stop_all(self) -> None:
        running = [uid for uid, sim in self._sims.items() if sim.running]
        for uid in running:
            self.stop(uid)
        await asyncio.gather(*(self._sims[uid].task for uid in running),
                             return_exceptions=True)

    def _terminate(self, proc: asyncio.subprocess.Process) -> None:
        proc.terminate()

        def _kill() -> None:
            if proc.returncode is None:
                log.warning('sim pid %d ignored SIGTERM; killing it', proc.pid)
                proc.kill()

        asyncio.get_running_loop().call_later(STOP_TIMEOUT_SEC, _kill)

    async def _run(self, uid: str, sim: _Sim, cmd: list[str]) -> None:
        env = child_env()
        env[STORAGE_ROOT_ENV] = str(self._storage_root)
        guard = RebootGuard()
        loop = asyncio.get_running_loop()
        while True:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd, env=env,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                )
            except OSError as e:
                log.error('%s: failed to start: %s', uid, e)
                self._finish(sim, f'failed to start: {e}')
                return
            sim.proc = proc
            sim.pid = proc.pid
            log.info('%s: started sim_device pid %d', uid, proc.pid)
            self.on_change()
            if sim.stopping:
                self._terminate(proc)

            # The sim logs to stderr; forward it so it lands in the joint log.
            assert proc.stdout is not None
            async for line in proc.stdout:
                log.info('%s: %s', uid, line.decode(errors='replace').rstrip())
            code = await proc.wait()

            if sim.stopping:
                log.info('%s: stopped', uid)
                self._finish(sim, None)
                return
            if code == 0:
                # SIGTERM/SIGINT from outside (a shell, ctrl-c): a plain off.
                log.info('%s: sim_device exited', uid)
                self._finish(sim, None)
                return
            if code != SIM_REBOOT_EXIT_CODE:
                reason = _describe_exit(code)
                log.warning('%s: sim_device %s', uid, reason)
                self._finish(sim, reason)
                return
            if guard.record(loop.time()):
                reason = f'gave up after {MAX_REBOOTS} reboots in {REBOOT_WINDOW_SEC:.0f}s'
                log.warning('%s: %s', uid, reason)
                self._finish(sim, reason)
                return
            log.info('%s: rebooting', uid)

    def _finish(self, sim: _Sim, last_exit: str | None) -> None:
        # Only this sim's own entry: a later start makes a new one.
        sim.running = False
        sim.pid = None
        sim.proc = None
        sim.last_exit = last_exit
        self.on_change()
