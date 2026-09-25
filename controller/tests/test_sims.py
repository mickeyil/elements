"""Tests for the web's sim process manager, against a stand-in sim_device."""

import asyncio
import logging
import os
import signal
import stat
import sys
import time

import pytest

from elemctl.sim import SIM_REBOOT_EXIT_CODE
from elemctl.sims import SimManager


def _stand_in(tmp_path):
    """An executable stand-in for sim_device. Once its SIGTERM handler is in
    place it appends its pid to 'pids' and logs a line; it exits with the
    reboot sentinel once if a 'reboot' file exists (consuming it), and on
    SIGTERM notes it in 'terms' and exits 0 like the real sim."""
    path = tmp_path / 'sim_device'
    path.write_text(
        f'#!{sys.executable}\n'
        'import os, pathlib, signal, sys, time\n'
        f'd = pathlib.Path({str(tmp_path)!r})\n'
        'def on_term(*_):\n'
        "    with open(d / 'terms', 'a') as f:\n"
        "        f.write('x\\n')\n"
        '    sys.exit(0)\n'
        'signal.signal(signal.SIGTERM, on_term)\n'
        "with open(d / 'pids', 'a') as f:\n"
        "    f.write(f'{os.getpid()}\\n')\n"
        "print('up', sys.argv[2], flush=True)\n"
        "if (d / 'reboot').exists():\n"
        "    (d / 'reboot').unlink()\n"
        f'    sys.exit({SIM_REBOOT_EXIT_CODE})\n'
        'while True:\n'
        '    time.sleep(1)\n'
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _lines(path):
    return path.read_text().splitlines() if path.exists() else []


async def _until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, 'timed out'
        await asyncio.sleep(0.02)


def _manager(tmp_path):
    changes = []
    manager = SimManager(lambda: changes.append(1), sim_device_bin=_stand_in(tmp_path),
                         storage_root=tmp_path)
    return manager, changes


def test_killed_sim_is_reported_and_can_start_again(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger='elemctl.sims')

    async def run():
        manager, changes = _manager(tmp_path)
        manager.start('sim-a')
        await _until(lambda: manager.state()['sim-a']['pid'] is not None)
        await _until(lambda: 'sim-a: up sim-a' in caplog.text)   # output is forwarded

        before = len(changes)
        os.kill(manager.state()['sim-a']['pid'], signal.SIGKILL)
        await _until(lambda: not manager.is_running('sim-a'))
        assert manager.state()['sim-a'] == {
            'running': False, 'pid': None, 'last_exit': 'killed by SIGKILL'}
        assert len(changes) > before

        manager.start('sim-a')
        assert manager.state()['sim-a']['last_exit'] is None
        await _until(lambda: manager.state()['sim-a']['pid'] is not None)
        await manager.stop_all()

    asyncio.run(run())


def test_reboot_sentinel_respawns(tmp_path):
    async def run():
        manager, _ = _manager(tmp_path)
        (tmp_path / 'reboot').write_text('')
        manager.start('sim-a')
        await _until(lambda: len(_lines(tmp_path / 'pids')) == 2)
        await _until(lambda: manager.state()['sim-a']['pid'] == int(_lines(tmp_path / 'pids')[1]))
        assert manager.is_running('sim-a')
        await manager.stop_all()

    asyncio.run(run())


def test_stop_terminates_cleanly(tmp_path):
    async def run():
        manager, _ = _manager(tmp_path)
        manager.start('sim-a')
        await _until(lambda: _lines(tmp_path / 'pids'))
        manager.stop('sim-a')
        await _until(lambda: not manager.is_running('sim-a'))
        assert manager.state()['sim-a']['last_exit'] is None
        assert (tmp_path / 'terms').read_text() == 'x\n'
        with pytest.raises(ValueError, match='not running'):
            manager.stop('sim-a')

    asyncio.run(run())


def test_outside_sigterm_is_a_plain_off(tmp_path):
    async def run():
        manager, _ = _manager(tmp_path)
        manager.start('sim-a')
        await _until(lambda: _lines(tmp_path / 'pids'))
        os.kill(manager.state()['sim-a']['pid'], signal.SIGTERM)
        await _until(lambda: not manager.is_running('sim-a'))
        assert manager.state()['sim-a']['last_exit'] is None

    asyncio.run(run())


def test_second_start_is_refused(tmp_path):
    async def run():
        manager, _ = _manager(tmp_path)
        manager.start('sim-a')
        with pytest.raises(ValueError, match='already running'):
            manager.start('sim-a')
        await _until(lambda: _lines(tmp_path / 'pids'))
        await asyncio.sleep(0.2)   # time for a second process to show up
        assert _lines(tmp_path / 'pids') == [str(manager.state()['sim-a']['pid'])]
        await manager.stop_all()

    asyncio.run(run())


def test_missing_binary_is_refused(tmp_path):
    manager = SimManager(sim_device_bin=tmp_path / 'sim_device')
    with pytest.raises(ValueError, match='not built'):
        manager.start('sim-a')
    assert manager.state() == {}
