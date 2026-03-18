"""CSV-backed 2D layout loading for sim devices."""

from __future__ import annotations

import csv
import logging
import os
from pathlib import Path

from .config import DeviceConfig

DEFAULT_LAYOUTS_PATH = '~/.config/elemctl/layouts'

log = logging.getLogger(__name__)


class LayoutError(ValueError):
    """Raised for invalid sim layout files."""


def parse_layout_csv(text: str, configured_length: int) -> dict:
    """Parse one CSV layout into a browser-facing rows payload."""
    lines = text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()

    rows: list[list[int | None]] = []
    seen: set[int] = set()
    max_width = 0

    for line in lines:
        parsed = next(csv.reader([line]))
        row: list[int | None] = []
        for raw_cell in parsed:
            cell = raw_cell.strip()
            if not cell:
                row.append(None)
                continue
            try:
                index = int(cell)
            except ValueError as e:
                raise LayoutError(f'invalid cell value: {raw_cell!r}') from e
            if index < 1:
                raise LayoutError(f'layout index must be >= 1, got {index}')
            if index > configured_length:
                raise LayoutError(
                    f'layout index must be <= configured length {configured_length}, got {index}'
                )
            if index in seen:
                raise LayoutError(f'duplicate layout index: {index}')
            seen.add(index)
            row.append(index)
        rows.append(row)
        max_width = max(max_width, len(row))

    if not seen:
        raise LayoutError('layout must include at least one active index')

    for row in rows:
        row.extend([None] * (max_width - len(row)))

    return {'rows': rows}


def load_layouts_for_devices(
    device_configs: list[DeviceConfig],
    layouts_dir: str = DEFAULT_LAYOUTS_PATH,
) -> dict[str, dict]:
    """Load layouts for configured sim devices, warning and skipping invalid files."""
    root = Path(os.path.expanduser(layouts_dir))
    layouts: dict[str, dict] = {}

    for dc in device_configs:
        if dc.device_type != 'sim':
            continue
        path = root / f'{dc.device_uid}.csv'
        try:
            text = path.read_text()
        except FileNotFoundError:
            continue
        except OSError as e:
            log.warning('ignoring unreadable layout for %s (%s): %s', dc.device_uid, path, e)
            continue
        try:
            layouts[dc.device_uid] = parse_layout_csv(text, dc.length)
        except LayoutError as e:
            log.warning('ignoring invalid layout for %s (%s): %s', dc.device_uid, path, e)

    return layouts
