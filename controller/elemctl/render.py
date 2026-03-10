"""Offline CLI renderer: compile DSL → render via C++ → format output."""

from __future__ import annotations

import argparse
import csv
import io
import json
import struct
import subprocess
import sys
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class StripInfo:
    strip_id: str
    length: int


@dataclass
class RenderFrame:
    t_rel: float
    frame_index: int           # 1-based
    strips: dict[str, list[list[float]]]  # strip_id → pixels → channel values


@dataclass
class RenderResult:
    fps: int
    beat: float
    duration: float
    channels: list[str]        # e.g. ["V"] or ["R", "G", "B"]
    strips: list[StripInfo]
    frames: list[RenderFrame]


# ---------------------------------------------------------------------------
# Step 1 — Compile
# ---------------------------------------------------------------------------

def compile_dsl(path: Path, beat: float, duration: float):
    """Compile DSL source file to CompiledManifest."""
    from elements.dsl import _builder, build_manifest
    _builder.reset()
    try:
        source = path.read_text()
        exec(source, {'__builtins__': __builtins__})
        return build_manifest(beat=beat, duration=duration)
    finally:
        _builder.reset()


# ---------------------------------------------------------------------------
# Step 3 — Render (C++ subprocess)
# ---------------------------------------------------------------------------

STRIP_RENDER_BIN = Path(__file__).resolve().parent.parent.parent / 'build' / 'strip_render'


def run_strip_render(blob: bytes, length: int, fps: int) -> bytes:
    """Run strip_render subprocess, return raw stdout bytes."""
    if not STRIP_RENDER_BIN.exists():
        raise FileNotFoundError(
            f"strip_render not found at {STRIP_RENDER_BIN} — run 'cmake --build build' first"
        )
    result = subprocess.run(
        [str(STRIP_RENDER_BIN), str(length), str(fps)],
        input=blob,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"strip_render failed: {result.stderr.decode()}")
    return result.stdout


def parse_strip_output(data: bytes, length: int) -> list[tuple[float, bytes]]:
    """Parse binary output into (t_rel, rgb_bytes) pairs."""
    frame_size = 4 + length * 3
    if len(data) == 0:
        raise RuntimeError("strip_render produced no output")
    if len(data) % frame_size != 0:
        raise RuntimeError(
            f"truncated strip_render output: {len(data)} bytes not divisible by frame_size {frame_size}"
        )
    frames = []
    offset = 0
    prev_t = -1.0
    while offset < len(data):
        t_rel = struct.unpack('=f', data[offset:offset + 4])[0]
        if not math.isfinite(t_rel):
            raise RuntimeError(
                f"non-finite t_rel at frame {len(frames)}: {t_rel}"
            )
        if t_rel <= prev_t:
            raise RuntimeError(
                f"non-monotonic t_rel: {t_rel} <= {prev_t} at frame {len(frames)}"
            )
        prev_t = t_rel
        rgb = data[offset + 4:offset + frame_size]
        frames.append((t_rel, rgb))
        offset += frame_size
    return frames


# ---------------------------------------------------------------------------
# Step 4 — Channel projection
# ---------------------------------------------------------------------------

def project_channels(raw_frames: list[tuple[float, bytes]], length: int,
                     channels: list[str]) -> list[list[list[float]]]:
    """Convert raw RGB frames to per-pixel channel values.

    Returns: list of frames, each frame is list of pixels, each pixel is list of channel values.
    """
    result = []
    for _, rgb_bytes in raw_frames:
        pixels = []
        for px in range(length):
            r = rgb_bytes[px * 3] / 255.0
            g = rgb_bytes[px * 3 + 1] / 255.0
            b = rgb_bytes[px * 3 + 2] / 255.0
            vals = []
            for ch in channels:
                if ch == 'V':
                    vals.append(max(r, g, b))
                elif ch == 'R':
                    vals.append(r)
                elif ch == 'G':
                    vals.append(g)
                elif ch == 'B':
                    vals.append(b)
            pixels.append(vals)
        result.append(pixels)
    return result


# ---------------------------------------------------------------------------
# Step 5 — Assemble frames
# ---------------------------------------------------------------------------

def build_result(strip_artifacts, raw_data: dict[str, list[tuple[float, bytes]]],
                 channels: list[str], fps: int, beat: float,
                 duration: float) -> RenderResult:
    """Merge per-strip raw data into RenderResult.

    Args:
        strip_artifacts: list of (strip_id, length) tuples
        raw_data: strip_id → parsed frames from parse_strip_output
        channels: channel list
        fps, beat, duration: metadata
    """
    strip_infos = [StripInfo(sid, length) for sid, length in strip_artifacts]

    # Validate all strips have same frame count
    counts = {sid: len(raw_data[sid]) for sid, _ in strip_artifacts}
    unique_counts = set(counts.values())
    if len(unique_counts) > 1:
        raise RuntimeError(f"strips produced different frame counts: {counts}")

    n_frames = next(iter(counts.values()))
    if n_frames == 0:
        return RenderResult(fps=fps, beat=beat, duration=duration,
                            channels=channels, strips=strip_infos, frames=[])

    # Project channels per strip
    projected = {}
    for sid, length in strip_artifacts:
        projected[sid] = project_channels(raw_data[sid], length, channels)

    # Build merged frames
    frames = []
    first_sid = strip_artifacts[0][0]
    for i in range(n_frames):
        t_rel = raw_data[first_sid][i][0]
        # Cross-strip t_rel validation
        for sid, _ in strip_artifacts[1:]:
            other_t = raw_data[sid][i][0]
            if abs(t_rel - other_t) > 1e-6:
                raise RuntimeError(
                    f"cross-strip t_rel divergence at row {i}: "
                    f"{first_sid}={t_rel}, {sid}={other_t}"
                )
        strip_data = {}
        for sid, _ in strip_artifacts:
            strip_data[sid] = projected[sid][i]
        frames.append(RenderFrame(t_rel=t_rel, frame_index=i + 1, strips=strip_data))

    return RenderResult(fps=fps, beat=beat, duration=duration,
                        channels=channels, strips=strip_infos, frames=frames)


# ---------------------------------------------------------------------------
# Step 6 — Time filtering
# ---------------------------------------------------------------------------

def filter_time_window(result: RenderResult, from_t: Optional[float],
                       to_t: Optional[float]) -> RenderResult:
    """Filter frames by time window, preserving original frame_index."""
    if from_t is None and to_t is None:
        return result
    filtered = []
    for f in result.frames:
        if from_t is not None and f.t_rel < from_t - 1e-9:
            continue
        if to_t is not None and f.t_rel > to_t + 1e-9:
            continue
        filtered.append(f)
    return RenderResult(fps=result.fps, beat=result.beat, duration=result.duration,
                        channels=result.channels, strips=result.strips, frames=filtered)


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def format_table(result: RenderResult) -> str:
    """Format as human-readable table. Single strip only."""
    if len(result.strips) != 1:
        raise ValueError("table format supports single strip only")

    strip = result.strips[0]
    bpm = 60.0 / result.beat if result.beat > 0 else 0
    beats = result.duration / result.beat if result.beat > 0 else 0
    frame_period = 1.0 / result.fps if result.fps > 0 else 0

    lines = []

    for ch_idx, ch in enumerate(result.channels):
        if len(result.channels) > 1:
            if ch_idx > 0:
                lines.append('')
            lines.append(f'channel: {ch}')
            lines.append('')

        if ch_idx == 0:
            lines.append(f'rendered channels: {",".join(result.channels)}')
            lines.append(f'rendering freq: {int(result.fps)} Hz')
            lines.append(f'beat: {result.beat}s ({bpm:.0f} bpm)')
            lines.append(f'duration: {result.duration}s ({beats:.0f} beats)')
            lines.append('')
            lines.append(f'strip {strip.strip_id}: {strip.length} leds')
            lines.append('')

        # Column header
        col_hdrs = [f'{i+1:>5d}' for i in range(strip.length)]
        lines.append(' ' * 17 + ''.join(col_hdrs))

        # Data rows
        for frame in result.frames:
            t = frame.t_rel
            mins = int(t) // 60
            secs = t - mins * 60
            ts = f'[{mins:02d}:{secs:06.3f} {frame.frame_index:4d}]'

            # Beat marker
            on_beat = False
            if result.beat > 0 and frame_period > 0:
                remainder = t % result.beat
                if remainder < frame_period * 0.5 or (result.beat - remainder) < frame_period * 0.5:
                    on_beat = True
            marker = ' B' if on_beat else '  '

            pixels = frame.strips[strip.strip_id]
            vals = ''.join(f'{px[ch_idx]:5.2f}' for px in pixels)
            lines.append(f'{ts}{marker} {vals}')

    return '\n'.join(lines) + '\n'


def format_csv(result: RenderResult) -> str:
    """Format as CSV."""
    out = io.StringIO()
    writer = csv.writer(out)

    # Header — use max strip length so all rows have consistent columns
    max_len = max(s.length for s in result.strips)
    header = ['t_rel', 'frame_index', 'strip']
    for px in range(max_len):
        for ch in result.channels:
            header.append(f'px{px}_{ch}')
    writer.writerow(header)

    # Rows
    n_channels = len(result.channels)
    for frame in result.frames:
        for strip_info in result.strips:
            row = [f'{frame.t_rel:.3f}', str(frame.frame_index), strip_info.strip_id]
            pixels = frame.strips[strip_info.strip_id]
            for px in range(max_len):
                if px < len(pixels):
                    for val in pixels[px]:
                        row.append(f'{val:.2f}')
                else:
                    row.extend([''] * n_channels)
            writer.writerow(row)

    return out.getvalue()


def format_json(result: RenderResult) -> str:
    """Format as JSON."""
    obj = {
        'fps': result.fps,
        'beat': result.beat,
        'duration': result.duration,
        'channels': result.channels,
        'strips': [{'strip_id': s.strip_id, 'length': s.length} for s in result.strips],
        'frames': [
            {
                't_rel': round(f.t_rel, 6),
                'frame_index': f.frame_index,
                'strips': {sid: [[round(v, 4) for v in px] for px in pixels]
                           for sid, pixels in f.strips.items()},
            }
            for f in result.frames
        ],
    }
    return json.dumps(obj, indent=2) + '\n'


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        prog='elemctl.render',
        description='Offline CLI renderer for Elements animations',
    )
    parser.add_argument('program', help='DSL source file path')
    parser.add_argument('--beat', type=float, required=True, help='Beat duration in seconds')
    parser.add_argument('--duration', type=float, required=True, help='Program duration in seconds')
    parser.add_argument('--fps', type=int, default=50, help='Rendering frequency (default: 50)')
    parser.add_argument('--channels', default='V',
                        help='Channels: V, R, G, B, or comma-separated (default: V)')
    parser.add_argument('--format', default='table', choices=['table', 'csv', 'json'],
                        dest='fmt', help='Output format (default: table)')
    parser.add_argument('--strip', default=None, help='Render only named strip')
    parser.add_argument('--from', type=float, default=None, dest='from_t',
                        help='Start time filter (seconds)')
    parser.add_argument('--to', type=float, default=None, dest='to_t',
                        help='End time filter (seconds)')
    parser.add_argument('-o', default=None, help='Output file path (default: stdout)')

    args = parser.parse_args(argv)

    # Validate
    if not math.isfinite(args.beat) or args.beat <= 0:
        parser.error('--beat must be a finite number > 0')
    if not math.isfinite(args.duration) or args.duration <= 0:
        parser.error('--duration must be a finite number > 0')
    if args.fps <= 0:
        parser.error('--fps must be > 0')
    if args.from_t is not None and not math.isfinite(args.from_t):
        parser.error('--from must be a finite number')
    if args.to_t is not None and not math.isfinite(args.to_t):
        parser.error('--to must be a finite number')

    channels = [c.strip().upper() for c in args.channels.split(',')]
    valid_channels = {'V', 'R', 'G', 'B'}
    for ch in channels:
        if ch not in valid_channels:
            parser.error(f'unknown channel: {ch}')
    if len(channels) != len(set(channels)):
        parser.error('duplicate channels')

    if args.from_t is not None and args.to_t is not None and args.from_t > args.to_t:
        parser.error('--from must be <= --to')

    # Step 1 — Compile
    program_path = Path(args.program)
    if not program_path.exists():
        print(f'Error: file not found: {program_path}', file=sys.stderr)
        sys.exit(1)

    try:
        manifest = compile_dsl(program_path, args.beat, args.duration)
    except Exception as e:
        print(f'Error: {e}', file=sys.stderr)
        sys.exit(1)

    # Step 2 — Filter strips
    artifacts = manifest.strips
    if args.strip is not None:
        artifacts = [a for a in artifacts if a.strip_id == args.strip]
        if not artifacts:
            available = [a.strip_id for a in manifest.strips]
            print(f'Error: strip {args.strip!r} not found (available: {available})',
                  file=sys.stderr)
            sys.exit(1)

    if args.fmt == 'table' and len(artifacts) > 1:
        print('Error: table format supports single strip only; use --strip to select one',
              file=sys.stderr)
        sys.exit(1)

    # Step 3 — Render each strip
    try:
        raw_data: dict[str, list[tuple[float, bytes]]] = {}
        for art in artifacts:
            raw = run_strip_render(art.blob, art.length, args.fps)
            raw_data[art.strip_id] = parse_strip_output(raw, art.length)

        # Step 5 — Assemble
        strip_arts = [(a.strip_id, a.length) for a in artifacts]
        result = build_result(strip_arts, raw_data, channels, args.fps, args.beat, args.duration)
    except Exception as e:
        print(f'Error: {e}', file=sys.stderr)
        sys.exit(1)

    # Step 6 — Time filter
    result = filter_time_window(result, args.from_t, args.to_t)

    # Step 7 — Format
    if args.fmt == 'table':
        output = format_table(result)
    elif args.fmt == 'csv':
        output = format_csv(result)
    else:
        output = format_json(result)

    try:
        if args.o:
            Path(args.o).write_text(output)
        else:
            sys.stdout.write(output)
    except OSError as e:
        print(f'Error: {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
