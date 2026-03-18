from pathlib import Path

import pytest

from elemctl.config import DeviceConfig
from elemctl.sim_layout import (
    LayoutError,
    _expand_line_cells,
    canonical_csv_hash,
    load_layout_for_editor,
    load_layouts_for_devices,
    parse_layout_csv,
    rows_to_canonical_csv,
    save_layout_for_editor,
)


def _sim_device(device_uid: str = 'sim-1', length: int = 10) -> DeviceConfig:
    return DeviceConfig(
        device_id=1,
        device_uid=device_uid,
        device_type='sim',
        host='127.0.0.1',
        tcp_port=9001,
        strip_id='main',
        length=length,
    )


def test_parse_layout_csv_accepts_sparse_layout():
    layout = parse_layout_csv("1,2,3,4\n,,,5\n8,7,6,\n", configured_length=10)

    assert layout == {
        'rows': [
            [1, 2, 3, 4],
            [None, None, None, 5],
            [8, 7, 6, None],
        ]
    }


def test_parse_layout_csv_normalizes_whitespace_and_blank_cells():
    layout = parse_layout_csv(" 1 ,  , 2 \n, 3 , \n", configured_length=5)

    assert layout == {
        'rows': [
            [1, None, 2],
            [None, 3, None],
        ]
    }


def test_parse_layout_csv_trailing_newline_does_not_add_empty_row():
    layout = parse_layout_csv("1,2\n3,4\n\n", configured_length=4)

    assert layout['rows'] == [
        [1, 2],
        [3, 4],
    ]


def test_parse_layout_csv_rejects_duplicate_index():
    with pytest.raises(LayoutError, match='duplicate layout index: 2'):
        parse_layout_csv("1,2,2\n", configured_length=5)


def test_parse_layout_csv_rejects_out_of_range_index():
    with pytest.raises(LayoutError, match='configured length 5'):
        parse_layout_csv("1,6\n", configured_length=5)


def test_parse_layout_csv_rejects_zero_index():
    with pytest.raises(LayoutError, match='>= 1'):
        parse_layout_csv("0,1\n", configured_length=5)


def test_load_layouts_for_devices_skips_missing_and_invalid(tmp_path, caplog):
    root = Path(tmp_path)
    (root / 'sim-1.csv').write_text("1,2\n3,4\n")
    (root / 'sim-2.csv').write_text("1,2,2\n")

    layouts = load_layouts_for_devices([
        _sim_device('sim-1', length=4),
        _sim_device('sim-2', length=4),
        DeviceConfig(
            device_id=3,
            device_uid='esp-1',
            device_type='esp32',
            host='127.0.0.1',
            tcp_port=9003,
            strip_id='main',
            length=4,
        ),
        _sim_device('sim-3', length=4),
    ], layouts_dir=str(root))

    assert layouts == {
        'sim-1': {
            'rows': [
                [1, 2],
                [3, 4],
            ]
        }
    }
    assert 'sim-2' not in layouts
    assert 'sim-3' not in layouts
    assert 'ignoring invalid layout for sim-2' in caplog.text


def test_rows_to_canonical_csv_trims_empty_border():
    csv_text = rows_to_canonical_csv(
        [
            [None, None, None, None],
            [None, 1, 2, None],
            [None, 3, 4, None],
            [None, None, None, None],
        ],
        configured_length=4,
    )

    assert csv_text == "1,2\n3,4\n"


def test_save_and_load_layout_for_editor_round_trip(tmp_path):
    saved = save_layout_for_editor(
        'sim-1',
        configured_length=4,
        layouts_dir=str(tmp_path),
        rows=[
            [None, None, None],
            [None, 1, 2],
            [None, 3, 4],
        ],
        editor_payload={
            'version': 1,
            'primitives': [
                {'type': 'single', 'index': 1, 'position': [0, 0]},
                {'type': 'single', 'index': 2, 'position': [1, 0]},
                {'type': 'single', 'index': 3, 'position': [0, 1]},
                {'type': 'single', 'index': 4, 'position': [1, 1]},
            ],
        },
    )

    expected_hash = canonical_csv_hash("1,2\n3,4\n")
    assert saved == {
        'rows': [
            [1, 2],
            [3, 4],
        ],
        'editor': {
            'version': 1,
            'csv_hash': expected_hash,
            'primitives': [
                {'type': 'single', 'index': 1, 'position': [0, 0]},
                {'type': 'single', 'index': 2, 'position': [1, 0]},
                {'type': 'single', 'index': 3, 'position': [0, 1]},
                {'type': 'single', 'index': 4, 'position': [1, 1]},
            ],
        },
    }

    loaded = load_layout_for_editor('sim-1', configured_length=4, layouts_dir=str(tmp_path))
    assert loaded == saved


def test_load_layout_for_editor_invalidates_drifted_meta(tmp_path):
    save_layout_for_editor(
        'sim-1',
        configured_length=4,
        layouts_dir=str(tmp_path),
        rows=[[1, 2], [3, 4]],
        editor_payload={
            'version': 1,
            'primitives': [
                {'type': 'single', 'index': 1, 'position': [0, 0]},
                {'type': 'single', 'index': 2, 'position': [1, 0]},
                {'type': 'single', 'index': 3, 'position': [0, 1]},
                {'type': 'single', 'index': 4, 'position': [1, 1]},
            ],
        },
    )
    (Path(tmp_path) / 'sim-1.csv').write_text("1,2\n4,3\n", encoding='utf-8')

    loaded = load_layout_for_editor('sim-1', configured_length=4, layouts_dir=str(tmp_path))

    assert loaded == {
        'rows': [
            [1, 2],
            [4, 3],
        ],
        'editor': None,
    }


def test_save_layout_for_editor_rejects_rows_editor_mismatch(tmp_path):
    with pytest.raises(LayoutError, match='editor primitives do not match rows'):
        save_layout_for_editor(
            'sim-1',
            configured_length=4,
            layouts_dir=str(tmp_path),
            rows=[[1, 2]],
            editor_payload={
                'version': 1,
                'primitives': [
                    {'type': 'single', 'index': 1, 'position': [1, 0]},
                    {'type': 'single', 'index': 2, 'position': [0, 0]},
                ],
            },
        )


@pytest.mark.parametrize(
    ('start', 'end', 'spacing', 'expected'),
    [
        ((0, 0), (4, 0), 0, [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)]),
        ((0, 0), (4, 0), 1, [(0, 0), (2, 0), (4, 0)]),
        ((0, 0), (0, 4), 1, [(0, 0), (0, 2), (0, 4)]),
        ((0, 0), (4, 4), 1, [(0, 0), (2, 2), (4, 4)]),
        ((4, 0), (0, 4), 0, [(4, 0), (3, 1), (2, 2), (1, 3), (0, 4)]),
    ],
)
def test_expand_line_cells_matches_expected_points(start, end, spacing, expected):
    assert _expand_line_cells(start, end, spacing) == expected


def test_save_and_load_layout_for_editor_round_trip_with_line(tmp_path):
    saved = save_layout_for_editor(
        'sim-1',
        configured_length=5,
        layouts_dir=str(tmp_path),
        rows=[[1, None, 2, None, 3]],
        editor_payload={
            'version': 1,
            'primitives': [
                {
                    'type': 'line',
                    'startIndex': 1,
                    'count': 3,
                    'spacing': 1,
                    'start': [0, 0],
                    'end': [4, 0],
                },
            ],
        },
    )

    assert saved['rows'] == [[1, None, 2, None, 3]]
    assert saved['editor']['primitives'] == [
        {
            'type': 'line',
            'startIndex': 1,
            'count': 3,
            'spacing': 1,
            'start': [0, 0],
            'end': [4, 0],
        },
    ]

    loaded = load_layout_for_editor('sim-1', configured_length=5, layouts_dir=str(tmp_path))
    assert loaded == saved


def test_save_layout_for_editor_rejects_line_count_mismatch(tmp_path):
    with pytest.raises(LayoutError, match='editor line count does not match expanded cells'):
        save_layout_for_editor(
            'sim-1',
            configured_length=5,
            layouts_dir=str(tmp_path),
            rows=[[1, None, 2, None, 3]],
            editor_payload={
                'version': 1,
                'primitives': [
                    {
                        'type': 'line',
                        'startIndex': 1,
                        'count': 4,
                        'spacing': 1,
                        'start': [0, 0],
                        'end': [4, 0],
                    },
                ],
            },
        )
