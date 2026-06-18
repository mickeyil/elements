"""Tests for elemctl.render — offline CLI renderer."""

import csv
import io
import json
import struct
import sys
from pathlib import Path

import pytest

_repo = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_repo / 'compiler'))

from elemctl.render import (
    STRIP_RENDER_BIN,
    RenderFrame,
    RenderResult,
    StripInfo,
    build_result,
    compile_dsl,
    filter_time_window,
    format_csv,
    format_json,
    format_table,
    main,
    parse_strip_output,
    project_channels,
    run_strip_render,
)

_requires_binary = pytest.mark.skipif(
    not STRIP_RENDER_BIN.exists(),
    reason=f'strip_render not built at {STRIP_RENDER_BIN}',
)

_SPARK_DSL = """\
from elements.dsl import strip, spark, sec
s = strip('test', length=5)
sp = spark(color='white', fade=1.0)
sp.schedule(s.pixels('0-4'), at=0, duration=sec(0.5))
"""

_SHIFT_DSL = """\
from elements.dsl import strip, paint, shift, sec
s = strip('test', length=5)
p = paint(colors=[(0,0,0.2),(0,0,0.4),(0,0,0.6),(0,0,0.8),(0,0,1.0)], format='hsv')
p.schedule(s.pixels('0-4'), at=0, duration=1)
sh = shift(direction='right', velocity=1, circular=False, fill='transparent')
sh.schedule(s.pixels('0-4'), at=sec(1.0), duration=sec(2.0), source=p)
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_rgb_frame(t_rel: float, rgb_triplets: list[tuple[int, int, int]]) -> bytes:
    """Build a single binary frame: [f32 t_rel][u8 r,g,b ...]"""
    data = struct.pack('=f', t_rel)
    for r, g, b in rgb_triplets:
        data += bytes([r, g, b])
    return data


def _make_result(n_frames=3, length=3, channels=None, beat=0.5, fps=50,
                 duration=1.0, strip_id='test', pixel_val=0.5) -> RenderResult:
    """Build a simple RenderResult for formatter tests."""
    if channels is None:
        channels = ['V']
    strips = [StripInfo(strip_id, length)]
    frames = []
    for i in range(n_frames):
        t = (i + 1) / fps
        pixels = [[pixel_val] * len(channels)] * length
        frames.append(RenderFrame(t_rel=t, frame_index=i + 1,
                                  strips={strip_id: pixels}))
    return RenderResult(fps=fps, beat=beat, duration=duration,
                        channels=channels, strips=strips, frames=frames)


# ---------------------------------------------------------------------------
# TestProjectChannels
# ---------------------------------------------------------------------------

class TestProjectChannels:
    def test_v_is_max_rgb(self):
        rgb = bytes([100, 200, 150])  # one pixel
        result = project_channels([(0.0, rgb)], 1, ['V'])
        assert len(result) == 1
        assert len(result[0]) == 1  # 1 pixel
        assert abs(result[0][0][0] - 200 / 255.0) < 1e-6

    def test_rgb_channels(self):
        rgb = bytes([50, 100, 200])
        result = project_channels([(0.0, rgb)], 1, ['R', 'G', 'B'])
        px = result[0][0]
        assert abs(px[0] - 50 / 255.0) < 1e-6
        assert abs(px[1] - 100 / 255.0) < 1e-6
        assert abs(px[2] - 200 / 255.0) < 1e-6


# ---------------------------------------------------------------------------
# TestParseStripOutput
# ---------------------------------------------------------------------------

class TestParseStripOutput:
    def test_valid_output(self):
        data = _make_rgb_frame(0.02, [(255, 0, 0)]) + _make_rgb_frame(0.04, [(0, 255, 0)])
        frames = parse_strip_output(data, 1)
        assert len(frames) == 2
        assert abs(frames[0][0] - 0.02) < 1e-5
        assert abs(frames[1][0] - 0.04) < 1e-5
        assert frames[0][1] == bytes([255, 0, 0])
        assert frames[1][1] == bytes([0, 255, 0])

    def test_truncated_output_raises(self):
        data = _make_rgb_frame(0.02, [(255, 0, 0)]) + b'\x00'
        with pytest.raises(RuntimeError, match='truncated'):
            parse_strip_output(data, 1)

    def test_empty_output_raises(self):
        with pytest.raises(RuntimeError, match='no output'):
            parse_strip_output(b'', 1)

    def test_non_monotonic_t_rel_raises(self):
        data = _make_rgb_frame(0.04, [(255, 0, 0)]) + _make_rgb_frame(0.02, [(0, 255, 0)])
        with pytest.raises(RuntimeError, match='non-monotonic'):
            parse_strip_output(data, 1)

    def test_nan_t_rel_raises(self):
        data = _make_rgb_frame(float('nan'), [(255, 0, 0)])
        with pytest.raises(RuntimeError, match='non-finite'):
            parse_strip_output(data, 1)

    def test_inf_t_rel_raises(self):
        data = _make_rgb_frame(float('inf'), [(255, 0, 0)])
        with pytest.raises(RuntimeError, match='non-finite'):
            parse_strip_output(data, 1)


# ---------------------------------------------------------------------------
# TestBuildResult
# ---------------------------------------------------------------------------

class TestBuildResult:
    def test_multi_strip_merge_by_row_index(self):
        f1 = _make_rgb_frame(0.02, [(255, 0, 0)])
        f2 = _make_rgb_frame(0.02, [(0, 255, 0)])
        raw = {
            'a': parse_strip_output(f1, 1),
            'b': parse_strip_output(f2, 1),
        }
        result = build_result([('a', 1), ('b', 1)], raw, ['V'], 50, 0.5, 1.0)
        assert len(result.frames) == 1
        assert 'a' in result.frames[0].strips
        assert 'b' in result.frames[0].strips

    def test_mismatched_frame_counts_errors(self):
        f1 = _make_rgb_frame(0.02, [(255, 0, 0)])
        f2 = _make_rgb_frame(0.02, [(0, 255, 0)]) + _make_rgb_frame(0.04, [(0, 0, 255)])
        raw = {
            'a': parse_strip_output(f1, 1),
            'b': parse_strip_output(f2, 1),
        }
        with pytest.raises(RuntimeError, match='different frame counts'):
            build_result([('a', 1), ('b', 1)], raw, ['V'], 50, 0.5, 1.0)

    def test_cross_strip_t_rel_divergence_errors(self):
        f1 = _make_rgb_frame(0.02, [(255, 0, 0)])
        f2 = _make_rgb_frame(0.05, [(0, 255, 0)])  # different t_rel
        raw = {
            'a': parse_strip_output(f1, 1),
            'b': parse_strip_output(f2, 1),
        }
        with pytest.raises(RuntimeError, match='divergence'):
            build_result([('a', 1), ('b', 1)], raw, ['V'], 50, 0.5, 1.0)


# ---------------------------------------------------------------------------
# TestFormatTable
# ---------------------------------------------------------------------------

class TestFormatTable:
    def test_table_header_and_values(self):
        result = _make_result(n_frames=2, length=3, pixel_val=0.75)
        output = format_table(result)
        assert 'rendering freq: 50 Hz' in output
        assert 'beat: 0.5s' in output
        assert 'duration: 1.0s' in output
        assert 'strip test: 3 leds' in output
        assert '0.75' in output

    def test_table_beat_markers(self):
        # At fps=50, beat=0.5, frame 25 (t=0.5) should be on beat
        result = _make_result(n_frames=30, length=2, beat=0.5, fps=50, duration=1.0)
        output = format_table(result)
        lines = output.split('\n')
        data_lines = [l for l in lines if l.startswith('[')]
        # frame 25: t=0.5s, should have B marker
        frame25 = [l for l in data_lines if ' 25]' in l]
        assert len(frame25) == 1
        assert ' B' in frame25[0]


# ---------------------------------------------------------------------------
# TestFormatCsv
# ---------------------------------------------------------------------------

class TestFormatCsv:
    def test_csv_columns_and_rows(self):
        result = _make_result(n_frames=3, length=2, channels=['V'])
        output = format_csv(result)
        reader = csv.reader(io.StringIO(output))
        rows = list(reader)
        header = rows[0]
        assert header == ['t_rel', 'frame_index', 'strip', 'px0_V', 'px1_V']
        assert len(rows) == 4  # header + 3 data rows

    def test_csv_empty_result(self):
        """Empty frames should produce header-only CSV."""
        result = RenderResult(fps=50, beat=0.5, duration=1.0, channels=['V'],
                              strips=[StripInfo('test', 3)], frames=[])
        output = format_csv(result)
        reader = csv.reader(io.StringIO(output))
        rows = list(reader)
        assert len(rows) == 1  # header only
        assert rows[0] == ['t_rel', 'frame_index', 'strip', 'px0_V', 'px1_V', 'px2_V']

    def test_csv_multi_strip_different_lengths(self):
        """Strips with different lengths produce consistent column counts."""
        strips = [StripInfo('short', 2), StripInfo('long', 4)]
        frames = [
            RenderFrame(t_rel=0.02, frame_index=1, strips={
                'short': [[0.5], [0.6]],
                'long': [[0.1], [0.2], [0.3], [0.4]],
            }),
        ]
        result = RenderResult(fps=50, beat=0.5, duration=1.0, channels=['V'],
                              strips=strips, frames=frames)
        output = format_csv(result)
        reader = csv.reader(io.StringIO(output))
        rows = list(reader)
        header = rows[0]
        # Header should have max(2,4)=4 pixel columns
        assert header == ['t_rel', 'frame_index', 'strip', 'px0_V', 'px1_V', 'px2_V', 'px3_V']
        assert len(rows) == 3  # header + 2 data rows (one per strip)
        # Short strip row should have empty cells for px2, px3
        short_row = rows[1]
        assert short_row[3:5] == ['0.50', '0.60']
        assert short_row[5:7] == ['', '']
        # Long strip row should have all values
        long_row = rows[2]
        assert long_row[3:7] == ['0.10', '0.20', '0.30', '0.40']


# ---------------------------------------------------------------------------
# TestFormatJson
# ---------------------------------------------------------------------------

class TestFormatJson:
    def test_json_round_trip(self):
        result = _make_result(n_frames=2, length=2, channels=['R', 'G'])
        output = format_json(result)
        parsed = json.loads(output)
        assert parsed['fps'] == 50
        assert parsed['channels'] == ['R', 'G']
        assert len(parsed['frames']) == 2
        assert 'test' in parsed['frames'][0]['strips']
        assert len(parsed['frames'][0]['strips']['test']) == 2  # 2 pixels
        assert len(parsed['frames'][0]['strips']['test'][0]) == 2  # 2 channels


# ---------------------------------------------------------------------------
# TestTimeFilter
# ---------------------------------------------------------------------------

class TestTimeFilter:
    def test_from_to_keeps_original_frame_index(self):
        result = _make_result(n_frames=10, length=2, fps=10, duration=1.0)
        # Frames at t=0.1, 0.2, ..., 1.0; filter to [0.3, 0.6]
        filtered = filter_time_window(result, 0.3, 0.6)
        assert len(filtered.frames) > 0
        for f in filtered.frames:
            assert 0.3 - 1e-6 <= f.t_rel <= 0.6 + 1e-6
        # frame_index should be original (not renumbered from 1)
        assert filtered.frames[0].frame_index == 3


# ---------------------------------------------------------------------------
# TestValidation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_table_rejects_multi_strip(self):
        result = RenderResult(
            fps=50, beat=0.5, duration=1.0, channels=['V'],
            strips=[StripInfo('a', 5), StripInfo('b', 5)],
            frames=[],
        )
        with pytest.raises(ValueError, match='single strip'):
            format_table(result)

    def test_unknown_strip_name_errors(self, tmp_path):
        prog = tmp_path / 'prog.py'
        prog.write_text(_SPARK_DSL)
        with pytest.raises(SystemExit):
            main([str(prog), '--beat', '0.5', '--duration', '0.5',
                  '--strip', 'nonexistent'])


# ---------------------------------------------------------------------------
# TestCliValidation
# ---------------------------------------------------------------------------

@pytest.fixture
def spark_file(tmp_path):
    prog = tmp_path / 'prog.py'
    prog.write_text(_SPARK_DSL)
    return str(prog)


class TestCliValidation:
    def test_invalid_fps(self, spark_file):
        with pytest.raises(SystemExit):
            main([spark_file, '--beat', '0.5', '--duration', '0.5', '--fps', '0'])

    def test_from_greater_than_to(self, spark_file):
        with pytest.raises(SystemExit):
            main([spark_file, '--beat', '0.5', '--duration', '0.5',
                  '--from', '0.5', '--to', '0.1'])

    def test_unknown_channel(self, spark_file):
        with pytest.raises(SystemExit):
            main([spark_file, '--beat', '0.5', '--duration', '0.5',
                  '--channels', 'X'])

    def test_duplicate_channels(self, spark_file):
        with pytest.raises(SystemExit):
            main([spark_file, '--beat', '0.5', '--duration', '0.5',
                  '--channels', 'V,V'])

    def test_invalid_beat(self, spark_file):
        with pytest.raises(SystemExit):
            main([spark_file, '--beat', '0', '--duration', '0.5'])

    def test_negative_duration(self, spark_file):
        with pytest.raises(SystemExit):
            main([spark_file, '--beat', '0.5', '--duration', '-1'])

    def test_nan_beat(self, spark_file):
        with pytest.raises(SystemExit):
            main([spark_file, '--beat', 'nan', '--duration', '0.5'])

    def test_inf_duration(self, spark_file):
        with pytest.raises(SystemExit):
            main([spark_file, '--beat', '0.5', '--duration', 'inf'])

    def test_nan_from(self, spark_file):
        with pytest.raises(SystemExit):
            main([spark_file, '--beat', '0.5', '--duration', '0.5',
                  '--from', 'nan'])

    def test_inf_to(self, spark_file):
        with pytest.raises(SystemExit):
            main([spark_file, '--beat', '0.5', '--duration', '0.5',
                  '--to', 'inf'])


# ---------------------------------------------------------------------------
# TestRenderStripBinary (requires C++ binary)
# ---------------------------------------------------------------------------

@_requires_binary
class TestRenderStripBinary:
    def test_spark_produces_frames(self, tmp_path):
        prog = tmp_path / 'prog.py'
        prog.write_text(_SPARK_DSL)
        manifest = compile_dsl(prog, beat=0.5, duration=0.5)
        art = next(iter(manifest.strips.values()))
        raw = run_strip_render(art.blob, art.length, 50)
        frames = parse_strip_output(raw, art.length)
        assert len(frames) >= 1
        # t_rel strictly increasing
        for i in range(1, len(frames)):
            assert frames[i][0] > frames[i - 1][0]
        # Final t_rel should not exceed duration + one frame period
        assert frames[-1][0] <= 0.5 + 1.0 / 50 + 1e-6

    def test_spark_v_decays(self, tmp_path):
        prog = tmp_path / 'prog.py'
        prog.write_text(_SPARK_DSL)
        manifest = compile_dsl(prog, beat=0.5, duration=0.5)
        art = next(iter(manifest.strips.values()))
        raw = run_strip_render(art.blob, art.length, 50)
        frames = parse_strip_output(raw, art.length)
        projected = project_channels(frames, art.length, ['V'])
        # Average V across pixels should decrease over time (spark decay)
        first_avg = sum(px[0] for px in projected[0]) / art.length
        last_avg = sum(px[0] for px in projected[-1]) / art.length
        assert first_avg > last_avg, f'V should decay: first={first_avg}, last={last_avg}'

    def test_fps_60_no_drift(self, tmp_path):
        """At fps=60, last frame t_rel should stay near the expected boundary."""
        prog = tmp_path / 'prog.py'
        prog.write_text(_SPARK_DSL)
        manifest = compile_dsl(prog, beat=0.5, duration=0.5)
        art = next(iter(manifest.strips.values()))
        raw = run_strip_render(art.blob, art.length, 60)
        frames = parse_strip_output(raw, art.length)
        assert len(frames) >= 1
        # Final t_rel should not exceed duration + one frame period
        assert frames[-1][0] <= 0.5 + 1.0 / 60 + 1e-6


# ---------------------------------------------------------------------------
# TestCliSmoke (end-to-end, requires C++ binary)
# ---------------------------------------------------------------------------

@_requires_binary
class TestCliSmoke:
    def test_table_output(self, tmp_path):
        prog = tmp_path / 'prog.py'
        prog.write_text(_SPARK_DSL)
        out = tmp_path / 'out.txt'
        main([str(prog), '--beat', '0.5', '--duration', '0.5',
              '--format', 'table', '-o', str(out)])
        output = out.read_text()
        assert 'rendering freq: 50 Hz' in output
        # At least one data row with bracket timestamp
        data_lines = [l for l in output.split('\n') if l.startswith('[')]
        assert len(data_lines) >= 1
