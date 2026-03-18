from pathlib import Path

import pytest

from elemctl.config import DeviceConfig
from elemctl.sim_layout import LayoutError, load_layouts_for_devices, parse_layout_csv


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
