"""Tests for Python slogger helper."""

import logging
from pathlib import Path

from elemctl.slogger import configure_logger


def test_configure_logger_creates_parent_directory(tmp_path):
    log_path = tmp_path / "nested" / "elemctl.log"
    root = logging.getLogger()
    old_handlers = root.handlers[:]
    old_level = root.level
    root.handlers = []
    try:
        configure_logger(logfile=str(log_path), console=False)
        assert log_path.parent.is_dir()
    finally:
        for handler in root.handlers[:]:
            handler.close()
        root.handlers = old_handlers
        root.setLevel(old_level)
