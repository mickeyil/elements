"""Tests for the parent-death helpers."""

import os

import pytest

from elemctl.procs import PARENT_PID_ENV, child_env, exit_with_parent


def test_child_env_names_us_as_parent():
    env = child_env({'A': '1'})
    assert env == {'A': '1', PARENT_PID_ENV: str(os.getpid())}


def test_exit_with_parent_is_a_noop_without_the_env(monkeypatch):
    monkeypatch.delenv(PARENT_PID_ENV, raising=False)
    exit_with_parent()


def test_exit_with_parent_continues_under_the_named_parent(monkeypatch):
    monkeypatch.setenv(PARENT_PID_ENV, str(os.getppid()))
    exit_with_parent()


def test_exit_with_parent_exits_when_the_parent_is_gone(monkeypatch):
    monkeypatch.setenv(PARENT_PID_ENV, str(os.getppid() + 1))
    with pytest.raises(SystemExit) as exc:
        exit_with_parent()
    assert exc.value.code == 0
