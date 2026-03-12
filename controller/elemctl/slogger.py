from __future__ import annotations

import datetime
import logging
import os
import sys


class _MillisFormatter(logging.Formatter):
    """Timestamp formatter with 3-digit milliseconds."""

    def formatTime(self, record, datefmt=None):
        dt = datetime.datetime.fromtimestamp(record.created)
        return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{int(record.msecs):03d}"


class _ColorFormatter(_MillisFormatter):
    """Console formatter with selective ANSI coloring."""

    _WARNING = "\033[93m"
    _ERROR = "\033[91m"
    _RESET = "\033[0m"
    _BASE = "[%(asctime)s] -%(levelname).1s- : %(message)s"

    def format(self, record):
        if record.levelno == logging.WARNING:
            fmt = self._WARNING + self._BASE + self._RESET
        elif record.levelno >= logging.ERROR:
            fmt = self._ERROR + self._BASE + self._RESET
        else:
            fmt = self._BASE

        original_fmt = self._style._fmt
        self._style._fmt = fmt
        try:
            return super().format(record)
        finally:
            self._style._fmt = original_fmt


def _parse_log_level(level_str: str) -> int:
    """Accept INFO/info/i style level strings."""

    level_map = {
        "debug": logging.DEBUG,
        "d": logging.DEBUG,
        "info": logging.INFO,
        "i": logging.INFO,
        "warning": logging.WARNING,
        "w": logging.WARNING,
        "error": logging.ERROR,
        "e": logging.ERROR,
        "critical": logging.CRITICAL,
        "c": logging.CRITICAL,
    }

    key = str(level_str).lower()
    if key not in level_map:
        valid = ", ".join(level_map.keys())
        raise ValueError(f"invalid log level: {level_str}. valid options: {valid}")
    return level_map[key]


def configure_logger(
    logfile: str | None = None,
    *,
    level: str = "INFO",
    console: bool = True,
    overwrite: bool = False,
) -> logging.Logger:
    """Configure the root logger for elemctl CLIs."""

    logger = logging.getLogger()
    log_level = _parse_log_level(level)
    logger.setLevel(log_level)

    has_console = any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in logger.handlers
    )
    has_file = any(isinstance(h, logging.FileHandler) for h in logger.handlers)

    if console and not has_console:
        console_handler = logging.StreamHandler(sys.stdout)
        if sys.stdout.isatty():
            console_handler.setFormatter(_ColorFormatter())
        else:
            console_handler.setFormatter(_MillisFormatter(_ColorFormatter._BASE))
        logger.addHandler(console_handler)
    elif not console and has_console:
        logger.handlers = [
            h for h in logger.handlers
            if not (
                isinstance(h, logging.StreamHandler)
                and not isinstance(h, logging.FileHandler)
            )
        ]

    if logfile and not has_file:
        mode = "w" if overwrite else "a"
        parent = os.path.dirname(os.path.abspath(os.path.expanduser(logfile)))
        if parent:
            os.makedirs(parent, exist_ok=True)
        file_handler = logging.FileHandler(logfile, mode=mode)
        file_handler.setFormatter(_MillisFormatter(_ColorFormatter._BASE))
        logger.addHandler(file_handler)

    for handler in logger.handlers:
        handler.setLevel(log_level)

    return logger
