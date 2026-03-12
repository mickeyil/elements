"""Tests for runtime version helper."""

import subprocess

from elemctl import version


def test_get_runtime_version_from_git(monkeypatch):
    version.get_runtime_version.cache_clear()

    def fake_run(*args, **kwargs):
        class Result:
            stdout = "v1.2.3-4-gabc123\n"
        return Result()

    monkeypatch.setattr(subprocess, 'run', fake_run)
    assert version.get_runtime_version() == 'v1.2.3-4-gabc123'


def test_get_runtime_version_falls_back_to_unknown(monkeypatch):
    version.get_runtime_version.cache_clear()

    def fake_run(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr(subprocess, 'run', fake_run)
    assert version.get_runtime_version() == 'unknown'
