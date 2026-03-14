"""Shared pytest hooks and fixtures for controller integration tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_RUNTIME_INTEGRATION_MARK = 'runtime_integration'
_ALLOW_BUSY_RUNTIME_ENV = 'ELEMCTL_TEST_ALLOW_BUSY_RUNTIME'


def pytest_configure(config):
    config.addinivalue_line(
        'markers',
        'runtime_integration: test uses real local runtime processes and '
        'requires an isolated environment',
    )


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Stash test outcome on the item node so fixtures can inspect it."""
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f'rep_{rep.when}', rep)


def _proc_argv(raw: bytes) -> list[str]:
    return [
        arg.decode(errors='replace')
        for arg in raw.split(b'\0')
        if arg
    ]


def _runtime_conflict_label(argv: list[str]) -> str | None:
    if not argv:
        return None

    exe = os.path.basename(argv[0])
    if exe == 'network_sim':
        return 'network_sim'

    def _elemctl_subcommand(subcommand: str) -> bool:
        for i, arg in enumerate(argv):
            if os.path.basename(arg) != 'elemctl':
                continue
            if i + 1 < len(argv) and argv[i + 1] == subcommand:
                return True
        return False

    if _elemctl_subcommand('sim'):
        return 'elemctl sim'
    if _elemctl_subcommand('server'):
        return 'elemctl server'

    if '-m' in argv:
        try:
            mod = argv[argv.index('-m') + 1]
        except IndexError:
            mod = ''
        if mod == 'elemctl.sim':
            return 'elemctl sim'
        if mod == 'elemctl.server':
            return 'elemctl server'
        if mod == 'elemctl':
            for arg in argv[argv.index('-m') + 2:]:
                if arg == 'sim':
                    return 'elemctl sim'
                if arg == 'server':
                    return 'elemctl server'

    if exe.startswith('python'):
        for i, arg in enumerate(argv[1:], start=1):
            if arg == 'elemctl.sim':
                return 'elemctl sim'
            if arg == 'elemctl.server':
                return 'elemctl server'
            if arg == 'elemctl' and i + 1 < len(argv) and argv[i + 1] == 'sim':
                return 'elemctl sim'
            if os.path.basename(arg) == 'elemctl' and i + 1 < len(argv) and argv[i + 1] == 'sim':
                return 'elemctl sim'
            if arg == 'elemctl' and i + 1 < len(argv) and argv[i + 1] == 'server':
                return 'elemctl server'
            if os.path.basename(arg) == 'elemctl' and i + 1 < len(argv) and argv[i + 1] == 'server':
                return 'elemctl server'

    return None


def _ancestor_pids() -> set[int]:
    pids = set()
    pid = os.getpid()
    while pid > 1 and pid not in pids:
        pids.add(pid)
        try:
            with open(f'/proc/{pid}/stat', 'r', encoding='utf-8') as f:
                stat = f.read()
        except OSError:
            break
        parts = stat.split()
        if len(parts) < 4:
            break
        try:
            pid = int(parts[3])
        except ValueError:
            break
    return pids


def _find_runtime_conflicts() -> list[str]:
    proc_root = Path('/proc')
    if not proc_root.is_dir():
        return []

    ignored_pids = _ancestor_pids()
    conflicts: list[str] = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid in ignored_pids:
            continue
        try:
            raw = (entry / 'cmdline').read_bytes()
        except OSError:
            continue
        argv = _proc_argv(raw)
        label = _runtime_conflict_label(argv)
        if label is None:
            continue
        conflicts.append(f'pid={pid} {" ".join(argv)}')
    return conflicts


@pytest.fixture(scope='session', autouse=True)
def _runtime_integration_isolation_guard(request):
    if os.environ.get(_ALLOW_BUSY_RUNTIME_ENV) == '1':
        return

    items = getattr(request.session, 'items', [])
    if not any(item.get_closest_marker(_RUNTIME_INTEGRATION_MARK) for item in items):
        return

    conflicts = _find_runtime_conflicts()
    if not conflicts:
        return

    pytest.fail(
        'runtime integration tests require an isolated local runtime; '
        'found external processes:\n'
        + '\n'.join(f'- {conflict}' for conflict in conflicts)
        + f'\nstop them and rerun, or set {_ALLOW_BUSY_RUNTIME_ENV}=1 to bypass',
        pytrace=False,
    )
