"""CSV-backed 2D layout loading for sim devices."""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
from pathlib import Path

from .config import DeviceConfig

DEFAULT_LAYOUTS_PATH = '~/.config/elemctl/layouts'

log = logging.getLogger(__name__)

_EDITOR_VERSION = 1


class LayoutError(ValueError):
    """Raised for invalid sim layout files."""


def _strip_trailing_empty_lines(lines: list[str]) -> list[str]:
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _normalize_rows_payload(
    rows: object,
    configured_length: int,
    *,
    require_nonempty: bool,
) -> list[list[int | None]]:
    if not isinstance(rows, list):
        raise LayoutError('rows must be a list')

    normalized_rows: list[list[int | None]] = []
    seen: set[int] = set()
    max_width = 0

    for raw_row in rows:
        if not isinstance(raw_row, list):
            raise LayoutError('rows must contain only lists')
        row: list[int | None] = []
        for raw_cell in raw_row:
            if raw_cell is None:
                row.append(None)
                continue
            if isinstance(raw_cell, bool) or not isinstance(raw_cell, int):
                raise LayoutError(f'invalid cell value: {raw_cell!r}')
            index = raw_cell
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
        normalized_rows.append(row)
        max_width = max(max_width, len(row))

    if require_nonempty and not seen:
        raise LayoutError('layout must include at least one active index')

    for row in normalized_rows:
        row.extend([None] * (max_width - len(row)))

    return normalized_rows


def rows_to_canonical_rows(rows: object, configured_length: int) -> list[list[int | None]]:
    normalized = _normalize_rows_payload(rows, configured_length, require_nonempty=False)
    if not normalized or not normalized[0]:
        raise LayoutError('layout must include at least one cell')
    return normalized


def parse_layout_csv(text: str, configured_length: int) -> dict:
    """Parse one CSV layout into a browser-facing rows payload."""
    lines = _strip_trailing_empty_lines(text.splitlines())
    parsed_rows: list[list[int | None]] = []

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
            row.append(index)
        parsed_rows.append(row)

    return {'rows': rows_to_canonical_rows(parsed_rows, configured_length)}


def rows_to_canonical_csv(rows: object, configured_length: int) -> str:
    canonical_rows = rows_to_canonical_rows(rows, configured_length)
    lines = [
        ','.join('' if cell is None else str(cell) for cell in row)
        for row in canonical_rows
    ]
    return '\n'.join(lines) + '\n'


def canonical_csv_hash(csv_text: str) -> str:
    return hashlib.sha256(csv_text.encode('utf-8')).hexdigest()


def _layout_paths(device_uid: str, layouts_dir: str) -> tuple[Path, Path]:
    root = Path(os.path.expanduser(layouts_dir))
    return root / f'{device_uid}.csv', root / f'{device_uid}.meta.json'


def _rows_to_single_primitives(rows: list[list[int | None]]) -> list[dict]:
    primitives: list[dict] = []
    for y, row in enumerate(rows):
        for x, cell in enumerate(row):
            if cell is None:
                continue
            primitives.append({
                'type': 'single',
                'index': cell,
                'position': [x, y],
            })
    primitives.sort(key=lambda item: item['index'])
    return primitives


def _parse_position(value: object, field_name: str) -> tuple[int, int]:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
    ):
        raise LayoutError(f'{field_name} must be a two-item integer list')
    x, y = value
    if x < 0 or y < 0:
        raise LayoutError(f'{field_name} coordinates must be >= 0')
    return x, y


def _expand_line_cells(
    start: tuple[int, int],
    end: tuple[int, int],
    spacing: int,
) -> list[tuple[int, int]]:
    if spacing < 0:
        raise LayoutError('editor line spacing must be >= 0')

    x0, y0 = start
    x1, y1 = end
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy

    path: list[tuple[int, int]] = []
    while True:
        path.append((x0, y0))
        if x0 == x1 and y0 == y1:
            break
        twice_err = err * 2
        if twice_err >= dy:
            err += dy
            x0 += sx
        if twice_err <= dx:
            err += dx
            y0 += sy

    step = spacing + 1
    return path[::step]


def _expand_editor_primitive_cells(
    primitive: dict,
    configured_length: int,
) -> tuple[dict, list[dict], list[tuple[int, int]]]:
    primitive_type = primitive.get('type')
    if primitive_type == 'single':
        index = primitive.get('index')
        if isinstance(index, bool) or not isinstance(index, int):
            raise LayoutError('editor primitive index must be an integer')
        if not (1 <= index <= configured_length):
            raise LayoutError(
                f'editor primitive index must be 1-{configured_length}, got {index}'
            )

        x, y = _parse_position(primitive.get('position'), 'editor primitive position')

        return (
            {
                'type': 'single',
                'index': index,
                'position': [x, y],
            },
            [
                {
                    'type': 'single',
                    'index': index,
                    'position': [x, y],
                }
            ],
            [(x, y)],
        )

    if primitive_type == 'inactive':
        x, y = _parse_position(primitive.get('position'), 'editor inactive position')
        return (
            {
                'type': 'inactive',
                'position': [x, y],
            },
            [],
            [(x, y)],
        )

    if primitive_type == 'line':
        start_index = primitive.get('startIndex')
        if isinstance(start_index, bool) or not isinstance(start_index, int):
            raise LayoutError('editor line startIndex must be an integer')
        if not (1 <= start_index <= configured_length):
            raise LayoutError(
                f'editor line startIndex must be 1-{configured_length}, got {start_index}'
            )

        count = primitive.get('count')
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise LayoutError('editor line count must be a positive integer')

        spacing = primitive.get('spacing')
        if isinstance(spacing, bool) or not isinstance(spacing, int) or spacing < 0:
            raise LayoutError('editor line spacing must be a non-negative integer')

        start = _parse_position(primitive.get('start'), 'editor line start')
        end = _parse_position(primitive.get('end'), 'editor line end')

        cells = _expand_line_cells(start, end, spacing)
        if len(cells) != count:
            raise LayoutError('editor line count does not match expanded cells')

        raw_inactive_offsets = primitive.get('inactiveOffsets')
        if raw_inactive_offsets is None:
            inactive_offsets: list[int] = []
        else:
            if not isinstance(raw_inactive_offsets, list):
                raise LayoutError('editor line inactiveOffsets must be a list')
            inactive_offsets = []
            for offset in raw_inactive_offsets:
                if isinstance(offset, bool) or not isinstance(offset, int):
                    raise LayoutError('editor line inactiveOffsets must contain integers')
                if offset < 0 or offset >= count:
                    raise LayoutError('editor line inactiveOffsets must be within the expanded line cell range')
                if offset in inactive_offsets:
                    raise LayoutError('editor line inactiveOffsets must be unique')
                inactive_offsets.append(offset)

        active_count = count - len(inactive_offsets)
        if active_count > 0 and start_index + active_count - 1 > configured_length:
            raise LayoutError(
                f'editor line indices must be 1-{configured_length}, got {start_index + active_count - 1}'
            )

        inactive_set = set(inactive_offsets)
        expanded_cells: list[dict] = []
        next_index = start_index
        for offset, (x, y) in enumerate(cells):
            if offset in inactive_set:
                continue
            expanded_cells.append({
                'type': 'single',
                'index': next_index,
                'position': [x, y],
            })
            next_index += 1

        return (
            {
                'type': 'line',
                'startIndex': start_index,
                'count': count,
                'spacing': spacing,
                'start': [start[0], start[1]],
                'end': [end[0], end[1]],
                **({'inactiveOffsets': inactive_offsets} if inactive_offsets else {}),
            },
            expanded_cells,
            cells,
        )

    raise LayoutError(f'unsupported editor primitive type: {primitive_type!r}')


def _normalize_editor_primitives(
    editor_payload: object,
    rows: list[list[int | None]],
    configured_length: int,
    *,
    expect_csv_hash: str | None,
) -> dict | None:
    if not isinstance(editor_payload, dict):
        raise LayoutError('editor payload must be an object')
    version = editor_payload.get('version')
    if version != _EDITOR_VERSION:
        raise LayoutError(f'editor version must be {_EDITOR_VERSION}')

    if expect_csv_hash is not None and editor_payload.get('csv_hash') != expect_csv_hash:
        return None

    primitives = editor_payload.get('primitives')
    if not isinstance(primitives, list):
        raise LayoutError('editor primitives must be a list')

    norm: list[dict] = []
    expanded: list[dict] = []
    seen_indices: set[int] = set()
    seen_positions: set[tuple[int, int]] = set()

    for primitive in primitives:
        if not isinstance(primitive, dict):
            raise LayoutError('editor primitive must be an object')
        normalized, expanded_cells, occupied_positions = _expand_editor_primitive_cells(
            primitive,
            configured_length,
        )

        for x, y in occupied_positions:
            if (x, y) in seen_positions:
                raise LayoutError(f'duplicate editor primitive position: {(x, y)}')
            seen_positions.add((x, y))

        for cell in expanded_cells:
            index = cell['index']
            if index in seen_indices:
                raise LayoutError(f'duplicate editor primitive index: {index}')
            seen_indices.add(index)

        norm.append(normalized)
        expanded.extend(expanded_cells)

    if sorted(expanded, key=lambda item: item['index']) != _rows_to_single_primitives(rows):
        raise LayoutError('editor primitives do not match rows')

    payload = {
        'version': _EDITOR_VERSION,
        'primitives': norm,
    }
    if expect_csv_hash is not None:
        payload['csv_hash'] = expect_csv_hash
    return payload


def load_layout_for_editor(
    device_uid: str,
    configured_length: int,
    layouts_dir: str = DEFAULT_LAYOUTS_PATH,
) -> dict | None:
    csv_path, meta_path = _layout_paths(device_uid, layouts_dir)
    try:
        csv_text = csv_path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return None

    rows = parse_layout_csv(csv_text, configured_length)['rows']
    expected_hash = canonical_csv_hash(rows_to_canonical_csv(rows, configured_length))

    try:
        meta_text = meta_path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return {'rows': rows, 'editor': None}
    except OSError as e:
        log.warning(
            'ignoring unreadable layout metadata for %s (%s): %s',
            device_uid,
            meta_path,
            e,
        )
        return {'rows': rows, 'editor': None}

    try:
        meta_payload = json.loads(meta_text)
    except json.JSONDecodeError as e:
        log.warning(
            'ignoring invalid layout metadata for %s (%s): %s',
            device_uid,
            meta_path,
            e,
        )
        return {'rows': rows, 'editor': None}

    try:
        editor = _normalize_editor_primitives(
            meta_payload,
            rows,
            configured_length,
            expect_csv_hash=expected_hash,
        )
    except LayoutError as e:
        log.warning(
            'ignoring invalid layout metadata for %s (%s): %s',
            device_uid,
            meta_path,
            e,
        )
        return {'rows': rows, 'editor': None}

    return {'rows': rows, 'editor': editor}


def save_layout_for_editor(
    device_uid: str,
    configured_length: int,
    layouts_dir: str,
    rows: object,
    editor_payload: object,
) -> dict:
    canonical_rows = rows_to_canonical_rows(rows, configured_length)
    csv_text = rows_to_canonical_csv(canonical_rows, configured_length)
    csv_hash = canonical_csv_hash(csv_text)

    editor = _normalize_editor_primitives(
        editor_payload,
        canonical_rows,
        configured_length,
        expect_csv_hash=None,
    )
    assert editor is not None
    editor['csv_hash'] = csv_hash

    csv_path, meta_path = _layout_paths(device_uid, layouts_dir)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text(csv_text, encoding='utf-8')
    meta_path.write_text(json.dumps(editor, separators=(',', ':')) + '\n', encoding='utf-8')

    return {
        'rows': canonical_rows,
        'editor': editor,
    }


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
