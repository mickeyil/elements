"""Shared pytest hooks and fixtures for controller integration tests."""

import pytest


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Stash test outcome on the item node so fixtures can inspect it."""
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f'rep_{rep.when}', rep)
