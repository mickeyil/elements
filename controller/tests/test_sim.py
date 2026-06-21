"""Tests for the elemctl sim supervisor."""

import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from elemctl.sim import (
    SIM_REBOOT_EXIT_CODE,
    build_sim_command,
    supervise,
    validate_sim_uid,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestValidateSimUid:
    def test_accepts_a_normal_uid(self):
        assert validate_sim_uid('sim-kitchen') is None

    def test_accepts_the_max_length(self):
        assert validate_sim_uid('sim-' + 'a' * 12) is None

    def test_rejects_empty(self):
        assert 'empty' in validate_sim_uid('')

    def test_rejects_missing_prefix(self):
        assert 'sim-' in validate_sim_uid('esp-123456789012')

    def test_rejects_over_long(self):
        assert 'longer' in validate_sim_uid('sim-' + 'a' * 13)

    def test_rejects_non_printable(self):
        assert 'printable' in validate_sim_uid('sim-a\tb')


class TestBuildSimCommand:
    def test_happy_path(self, tmp_path):
        fake_bin = tmp_path / 'sim_device'
        fake_bin.write_text('')

        cmd = build_sim_command(
            'sim-1',
            controller_host='10.0.0.7',
            frame_port=7000,
            sim_device_bin=fake_bin,
        )

        assert cmd == [
            str(fake_bin),
            '--device-uid', 'sim-1',
            '--controller-host', '10.0.0.7',
            '--frame-port', '7000',
        ]

    def test_rejects_missing_binary(self, tmp_path):
        with pytest.raises(ValueError, match='not built'):
            build_sim_command('sim-1', sim_device_bin=tmp_path / 'sim_device')

    def test_rejects_bad_frame_port(self, tmp_path):
        fake_bin = tmp_path / 'sim_device'
        fake_bin.write_text('')
        with pytest.raises(ValueError, match='frame-port'):
            build_sim_command('sim-1', frame_port=0, sim_device_bin=fake_bin)


def _stub_cmd(tmp_path, codes):
    """A child command that exits with codes[i] on its i-th run.

    Runs past the end of the list reuse the last code. The run count
    is the number of lines in the returned file.
    """
    runs = tmp_path / 'runs'
    script = (
        'import sys, pathlib\n'
        f'p = pathlib.Path({str(runs)!r})\n'
        "n = len(p.read_text().splitlines()) if p.exists() else 0\n"
        "p.open('a').write('x\\n')\n"
        f'codes = {list(codes)!r}\n'
        'sys.exit(codes[min(n, len(codes) - 1)])\n'
    )
    return [sys.executable, '-c', script], runs


def _run_count(runs: Path) -> int:
    return len(runs.read_text().splitlines()) if runs.exists() else 0


class TestSupervise:
    def test_clean_exit_runs_once(self, tmp_path):
        cmd, runs = _stub_cmd(tmp_path, [0])
        assert supervise(cmd) == 0
        assert _run_count(runs) == 1

    def test_error_exit_surfaces_without_restart(self, tmp_path):
        cmd, runs = _stub_cmd(tmp_path, [5])
        assert supervise(cmd) == 5
        assert _run_count(runs) == 1

    def test_reboot_sentinel_restarts(self, tmp_path):
        cmd, runs = _stub_cmd(tmp_path, [SIM_REBOOT_EXIT_CODE, SIM_REBOOT_EXIT_CODE, 0])
        assert supervise(cmd) == 0
        assert _run_count(runs) == 3

    def test_crash_loop_guard_gives_up(self, tmp_path):
        cmd, runs = _stub_cmd(tmp_path, [SIM_REBOOT_EXIT_CODE])
        assert supervise(cmd, max_reboots=3, reboot_window_sec=1000.0) == 1
        assert _run_count(runs) == 3

    def test_slow_reboots_do_not_trip_the_guard(self, tmp_path):
        cmd, runs = _stub_cmd(tmp_path, [SIM_REBOOT_EXIT_CODE] * 4 + [0])
        # Fake clock: each reboot lands far outside the window.
        ticks = iter(range(0, 10_000, 1_000))
        assert supervise(
            cmd, max_reboots=3, reboot_window_sec=30.0,
            clock=lambda: float(next(ticks)),
        ) == 0
        assert _run_count(runs) == 5

    def test_sigterm_is_forwarded_and_stops_the_loop(self, tmp_path):
        # supervise() installs a signal handler, so exercise it in its
        # own process and SIGTERM that.
        ready = tmp_path / 'ready'
        child = (
            'import signal, sys, time, pathlib\n'
            'signal.signal(signal.SIGTERM, lambda *a: sys.exit(7))\n'
            f'pathlib.Path({str(ready)!r}).write_text("up")\n'
            'time.sleep(30)\n'
        )
        outer = (
            'import sys\n'
            f"sys.path.insert(0, {str(REPO_ROOT / 'compiler')!r})\n"
            f"sys.path.insert(0, {str(REPO_ROOT / 'controller')!r})\n"
            'from elemctl.sim import supervise\n'
            f'sys.exit(supervise([sys.executable, "-c", {child!r}]))\n'
        )
        proc = subprocess.Popen([sys.executable, '-c', outer])
        try:
            deadline = time.monotonic() + 10.0
            while not ready.exists():
                assert time.monotonic() < deadline, 'sim child never came up'
                time.sleep(0.05)
            proc.send_signal(signal.SIGTERM)
            assert proc.wait(timeout=10) == 7
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
