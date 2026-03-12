"""Minimal TUI shell for elemctl — connects to the controller service over UDS.

Usage: python -m elemctl.tui [--socket PATH]
"""

from __future__ import annotations

import argparse
import ast
import datetime
import json
import math
import os
import queue
import select
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import HSplit, VerticalAlign, Window
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.layout import Layout

from .config import (
    DEFAULT_ANIMATIONS_PATH,
    DEFAULT_LOGS_PATH,
    DEFAULT_SOCKET_PATH,
    resolve_runtime_path,
)
from .uds_client import UdsClient
from .uds_wire import KIND_FRAME, KIND_JSON, parse_json_payload


# ------------------------------------------------------------------
# Animation metadata
# ------------------------------------------------------------------

@dataclass
class AnimationEntry:
    name: str           # stem, e.g. "spark_demo"
    path: str           # absolute path
    beat: float | None
    duration: float | None
    error: str | None   # set if metadata extraction failed


def extract_metadata(path: str) -> tuple[float, float]:
    """Extract BEAT and DURATION from a DSL animation file using ast.

    Returns (beat, duration). Raises ValueError on any problem.
    """
    try:
        source = Path(path).read_text()
    except OSError as e:
        raise ValueError(f"cannot read file: {e}")

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as e:
        raise ValueError(f"syntax error: {e}")

    beat = None
    duration = None

    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        if target.id not in ('BEAT', 'DURATION'):
            continue
        if not isinstance(node.value, ast.Constant):
            raise ValueError(
                f"{target.id} must be a numeric literal"
            )
        val = node.value.value
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            raise ValueError(
                f"{target.id} must be a numeric literal, got {type(val).__name__}"
            )
        if isinstance(val, float) and (math.isinf(val) or math.isnan(val)):
            raise ValueError(f"{target.id} must be finite")
        if val <= 0:
            raise ValueError(f"{target.id} must be positive, got {val}")
        if target.id == 'BEAT':
            if beat is not None:
                raise ValueError("duplicate BEAT assignment")
            beat = val
        else:
            if duration is not None:
                raise ValueError("duplicate DURATION assignment")
            duration = val

    if beat is None:
        raise ValueError("missing BEAT")
    if duration is None:
        raise ValueError("missing DURATION")

    return beat, duration


def scan_animations(directory: str) -> list[AnimationEntry]:
    """Scan a directory for .py animation files. Returns sorted entries."""
    expanded = os.path.expanduser(directory)
    if not os.path.isdir(expanded):
        return []

    entries: list[AnimationEntry] = []
    for p in sorted(Path(expanded).glob('*.py')):
        try:
            beat, duration = extract_metadata(str(p))
            entries.append(AnimationEntry(
                name=p.stem, path=str(p),
                beat=beat, duration=duration, error=None,
            ))
        except ValueError as e:
            entries.append(AnimationEntry(
                name=p.stem, path=str(p),
                beat=None, duration=None, error=str(e),
            ))

    return entries


# ------------------------------------------------------------------
# Command parsing
# ------------------------------------------------------------------

_QUIT_SENTINEL = object()
_RESCAN_SENTINEL = object()
_HELP_SENTINEL = object()

_COMMANDS = {
    'status', 'play', 'pause', 'stop', 'shutdown',
}


def parse_command(text: str, next_id: int) -> tuple[dict | None | object, str | None]:
    """Parse input text into a command dict.

    Returns (cmd_dict, error_string).
    - cmd_dict is a dict to send, _QUIT_SENTINEL for /quit, or None on error.
    - error_string is set when cmd_dict is None.
    """
    text = text.strip()
    if not text:
        return None, None  # empty input, no error

    if not text.startswith('/'):
        return None, "commands start with /  (try /status)"

    body = text[1:].strip()
    if not body:
        return None, "empty command"

    parts = body.split(None, 1)
    name = parts[0].lower()

    if name in {'quit', 'exit'}:
        if len(parts) > 1:
            return None, f"/{name} does not take arguments"
        return _QUIT_SENTINEL, None

    if name == 'help':
        if len(parts) > 1:
            return None, "/help does not take arguments"
        return _HELP_SENTINEL, None

    if name == 'rescan':
        if len(parts) > 1:
            return None, "/rescan does not take arguments"
        return _RESCAN_SENTINEL, None

    if name == 'load':
        return _parse_load(parts, next_id)

    if name not in _COMMANDS:
        return None, f"unknown command: /{name}"

    if len(parts) > 1:
        return None, f"/{name} does not take arguments"

    return {'cmd': name, 'id': next_id}, None


def _parse_load(parts: list[str], next_id: int) -> tuple[dict | None, str | None]:
    """Parse /load <target> [loop]."""
    if len(parts) < 2:
        return None, "/load requires a target (name or #index)"

    args = parts[1].split()
    target_str = args[0]
    rest = args[1:]

    # Parse loop flag
    loop = False
    if rest:
        if len(rest) > 1 or rest[0].lower() != 'loop':
            return None, f"/load: unexpected arguments: {' '.join(rest)}"
        loop = True

    # Parse target: #N or name
    if target_str.startswith('#'):
        idx_str = target_str[1:]
        try:
            idx = int(idx_str)
        except ValueError:
            return None, f"/load: invalid index: {target_str}"
        if idx < 1:
            return None, f"/load: index must be >= 1, got {idx}"
        return {'cmd': 'load', 'target': None, 'index': idx, 'loop': loop, 'id': next_id}, None

    return {'cmd': 'load', 'target': target_str, 'index': None, 'loop': loop, 'id': next_id}, None


# ------------------------------------------------------------------
# Event formatting
# ------------------------------------------------------------------

def _format_timestamp(now: datetime.datetime | None = None) -> str:
    dt = now or datetime.datetime.now()
    return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


def format_transcript_line(
    source: str,
    message: str,
    *,
    now: datetime.datetime | None = None,
) -> str:
    return f"[{_format_timestamp(now)}] [{source}] {message}"


def _format_device_summary(dev: dict) -> str:
    uid = msg_get(dev, 'device_uid', '?')
    strip = msg_get(dev, 'strip', '?')
    length = dev.get('length')
    if isinstance(length, int) and not isinstance(length, bool):
        return f"{uid} ({strip}, {length} LEDs)"
    return f"{uid} ({strip})"


def msg_get(msg: dict, key: str, default):
    return msg.get(key, default)


def format_event(kind: int, payload: bytes) -> str | None:
    """Format a UDS message as a single-line log entry. Returns None for frames."""
    if kind == KIND_FRAME:
        return None  # silently counted, not displayed

    if kind != KIND_JSON:
        return f"unknown message kind={kind} len={len(payload)}"

    try:
        msg = parse_json_payload(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return f"bad json message len={len(payload)}"

    msg_type = msg.get('type')

    if msg_type == 'reply':
        rid = msg.get('id')
        if msg.get('ok'):
            result = msg.get('result', {})
            return f"reply {rid} ok {json.dumps(result, separators=(',', ':'))}"
        else:
            return f"reply {rid} ERROR: {msg.get('error', '?')}"

    if msg_type == 'event':
        return _format_controller_event(msg)

    return f"unknown message {json.dumps(msg, separators=(',', ':'))}"


def _format_controller_event(msg: dict) -> str:
    event = msg.get('event')

    if event == 'snapshot':
        online = msg.get('online_count', '?')
        expected = msg.get('expected_count', '?')
        devices = msg.get('devices', [])
        connected = [
            _format_device_summary(dev)
            for dev in devices
            if dev.get('connected')
        ]
        offline = [
            _format_device_summary(dev)
            for dev in devices
            if not dev.get('connected')
        ]
        device_text = f"devices online {online}/{expected}"
        if connected:
            device_text += f": {', '.join(connected)}"
        else:
            device_text += ": none"
        if offline:
            device_text += f"; offline: {', '.join(offline)}"
        session = msg.get('session')
        if session:
            state = session.get('playback_state', '?')
            sid = session.get('session_id')
            t_rel = session.get('current_t_rel')
            duration = session.get('duration')
            if isinstance(t_rel, (int, float)) and isinstance(duration, (int, float)):
                return f"{state} session={sid} t={t_rel:.2f}/{duration:.2f}s; {device_text}"
            return f"{state} session={sid}; {device_text}"
        return f"idle, no active session; {device_text}"

    if event == 'state':
        state = msg.get('state', '?')
        epoch = msg.get('epoch', '?')
        sid = msg.get('session_id', '?')
        return f"state {state} epoch={epoch} session={sid}"

    if event == 'device_status':
        uid = msg.get('device_uid', '?')
        strip = msg.get('strip', '?')
        length = msg.get('length')
        connected = msg.get('connected')
        status = 'connected' if connected else 'disconnected'
        if isinstance(length, int) and not isinstance(length, bool):
            return f"device {uid} {status} ({strip}, {length} LEDs)"
        return f"device {uid} {status} ({strip})"

    if event == 'session_start':
        sid = msg.get('session_id', '?')
        epoch = msg.get('epoch', '?')
        duration = msg.get('duration', '?')
        strips = ', '.join(s.get('name', '?') for s in msg.get('strips', []))
        return f"session {sid} started epoch={epoch} duration={duration}s strips=[{strips}]"

    if event == 'loop':
        epoch = msg.get('epoch', '?')
        sid = msg.get('session_id', '?')
        return f"loop epoch={epoch} session={sid}"

    if event == 'error':
        return f"error: {msg.get('message', '?')}"

    return f"unknown event {json.dumps(msg, separators=(',', ':'))}"


# ------------------------------------------------------------------
# TUI application
# ------------------------------------------------------------------

class TuiApp:
    """Full-screen TUI shell for elemctl."""

    def __init__(
        self,
        socket_path: str,
        animations_dir: str = DEFAULT_ANIMATIONS_PATH,
        log_file: str | None = None,
    ):
        self._socket_path = socket_path
        self._animations_dir = animations_dir
        self._animation_list: list[AnimationEntry] = []
        self._client: UdsClient | None = None
        self._shutdown = threading.Event()
        self._next_id = 1
        self._log_lines: list[str] = []
        self._pending_logs: queue.Queue[str] = queue.Queue()
        self._reader_thread: threading.Thread | None = None
        self._log_fp = (
            open(log_file, 'a', encoding='utf-8', buffering=1)
            if log_file is not None
            else None
        )

        # prompt_toolkit widgets
        self._log_buffer = Buffer(read_only=True)
        input_control = BufferControl(buffer=Buffer(
            name='input',
            accept_handler=self._on_input,
            multiline=False,
        ))
        self._input_buffer = input_control.buffer

        kb = KeyBindings()

        @kb.add('c-c')
        @kb.add('c-q')
        def _(event):
            self._exit()

        log_window = Window(
            content=BufferControl(buffer=self._log_buffer),
            wrap_lines=True,
            dont_extend_height=True,
        )

        self._app = Application(
            layout=Layout(
                HSplit(
                    [
                        log_window,
                        Window(height=1, char='─', style='class:separator'),
                        Window(height=1, content=input_control),
                    ],
                    align=VerticalAlign.BOTTOM,
                ),
                focused_element=input_control,
            ),
            key_bindings=kb,
            full_screen=True,
            before_render=self._drain_pending_logs,
        )

    def run(self) -> None:
        """Connect and run the TUI. Blocks until exit."""
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        try:
            try:
                self._client = UdsClient(self._socket_path)
            except (OSError, ConnectionError) as e:
                self._local_log(f"connection failed: {e}")
                self._local_log("start the service with: ./elemctl serve")
                self._local_log("press Ctrl-C to exit")
                self._app.run()
                return

            self._local_log(f"connected to {self._socket_path}")
            self._reader_thread.start()
            self._app.run()
        finally:
            self._shutdown.set()
            if self._client is not None:
                self._client.close()
            if self._log_fp is not None:
                self._log_fp.close()

    # ------------------------------------------------------------------
    # Input handling
    # ------------------------------------------------------------------

    def _on_input(self, buff: Buffer) -> None:
        text = buff.text
        cmd, error = parse_command(text, self._next_id)

        if cmd is None:
            if error:
                self._local_log(error)
            return

        if cmd is _QUIT_SENTINEL:
            self._exit()
            return

        if cmd is _HELP_SENTINEL:
            self._show_help()
            return

        if cmd is _RESCAN_SENTINEL:
            self._do_rescan()
            return

        if isinstance(cmd, dict) and cmd.get('cmd') == 'load':
            self._do_load(cmd)
            return

        self._local_log(f"> {text}")
        self._next_id += 1

        if self._client is None:
            self._local_log("not connected")
            return

        try:
            self._client.send_cmd(cmd)
        except OSError as e:
            self._local_log(f"send failed: {e}")

    # ------------------------------------------------------------------
    # /help, /rescan and /load
    # ------------------------------------------------------------------

    def _show_help(self) -> None:
        self._local_log("commands:")
        self._local_log("  /help               show this help")
        self._local_log("  /status             show controller status")
        self._local_log("  /play               start playback")
        self._local_log("  /pause              pause playback")
        self._local_log("  /stop               stop playback")
        self._local_log("  /shutdown           stop the controller service")
        self._local_log("  /rescan             rescan the animations directory")
        self._local_log("  /load NAME [loop]   load an animation by name")
        self._local_log("  /load #N [loop]     load an animation by list index")
        self._local_log("  /quit, /exit        quit the TUI")

    def _do_rescan(self) -> None:
        self._animation_list = scan_animations(self._animations_dir)
        entries = self._animation_list
        if not entries:
            self._local_log(f"no animations in {self._animations_dir}")
            return
        self._local_log(
            f"{len(entries)} animation(s) in {self._animations_dir}:"
        )
        for i, e in enumerate(entries, 1):
            if e.error:
                self._local_log(f"  #{i}  {e.name:<16s} ERROR: {e.error}")
            else:
                self._local_log(
                    f"  #{i}  {e.name:<16s} beat={e.beat} duration={e.duration}"
                )

    def _do_load(self, cmd: dict) -> None:
        # Auto-rescan if list is empty
        if not self._animation_list:
            self._animation_list = scan_animations(self._animations_dir)

        index = cmd.get('index')
        target = cmd.get('target')
        loop = cmd.get('loop', False)

        if index is not None:
            if index < 1 or index > len(self._animation_list):
                self._local_log(
                    f"index #{index} out of range "
                    f"(have {len(self._animation_list)} animations)"
                )
                return
            entry = self._animation_list[index - 1]
        else:
            matches = [e for e in self._animation_list if e.name == target]
            if not matches:
                self._local_log(f"animation not found: {target}")
                return
            entry = matches[0]

        if entry.error:
            self._local_log(f"cannot load {entry.name}: {entry.error}")
            return

        try:
            source = Path(entry.path).read_text()
        except OSError as e:
            self._local_log(f"cannot read {entry.path}: {e}")
            return

        wire_cmd = {
            'cmd': 'load',
            'source': source,
            'beat': entry.beat,
            'duration': entry.duration,
            'loop': loop,
            'id': self._next_id,
        }
        loop_str = ' loop' if loop else ''
        self._local_log(f"> /load {entry.name}{loop_str}")
        self._next_id += 1

        if self._client is None:
            self._local_log("not connected")
            return

        try:
            self._client.send_cmd(wire_cmd)
        except OSError as e:
            self._local_log(f"send failed: {e}")

    # ------------------------------------------------------------------
    # Reader thread
    # ------------------------------------------------------------------

    def _reader_loop(self) -> None:
        client = self._client
        if client is None:
            return

        while not self._shutdown.is_set():
            try:
                readable, _, _ = select.select([client.fileno()], [], [], 0.1)
            except (OSError, ValueError):
                break

            if not readable:
                continue

            try:
                messages = client.recv_once()
            except (ConnectionError, OSError):
                self._enqueue_log(format_transcript_line('tui', 'disconnected'))
                break

            for kind, payload in messages:
                if kind == KIND_FRAME:
                    continue
                line = format_event(kind, payload)
                if line is not None:
                    self._enqueue_log(format_transcript_line('ctrl', line))

    # ------------------------------------------------------------------
    # Log pane
    # ------------------------------------------------------------------

    def _local_log(self, message: str) -> None:
        self._append_log(format_transcript_line('tui', message))

    def _write_transcript_line(self, line: str) -> None:
        if self._log_fp is None:
            return
        self._log_fp.write(line + '\n')
        self._log_fp.flush()

    def _enqueue_log(self, line: str) -> None:
        """Thread-safe: push a log line for the UI thread to drain."""
        self._pending_logs.put(line)
        self._app.invalidate()

    def _append_log(self, line: str) -> None:
        """UI-thread only: directly append a log line and update the buffer."""
        self._log_lines.append(line)
        self._write_transcript_line(line)
        self._flush_log_buffer()

    def _drain_pending_logs(self, app) -> None:
        """Called by prompt_toolkit before each render (UI thread)."""
        drained = False
        while True:
            try:
                line = self._pending_logs.get_nowait()
            except queue.Empty:
                break
            self._log_lines.append(line)
            self._write_transcript_line(line)
            drained = True
        if drained:
            self._flush_log_buffer()

    def _flush_log_buffer(self) -> None:
        text = '\n'.join(self._log_lines)
        self._log_buffer.set_document(
            self._log_buffer.document.__class__(text, len(text)),
            bypass_readonly=True,
        )

    # ------------------------------------------------------------------
    # Exit
    # ------------------------------------------------------------------

    def _exit(self) -> None:
        self._shutdown.set()
        self._app.exit()


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog='elemctl.tui',
        description='Elements controller TUI shell',
    )
    parser.add_argument(
        '--socket', default=DEFAULT_SOCKET_PATH,
        help='UDS socket path (default: %(default)s)',
    )
    parser.add_argument(
        '--animations', default=None,
        help='Animations directory (default: <repo>/animations/)',
    )
    parser.add_argument(
        '--config', default=None,
        help='Config file (used to read animations_dir if --animations not set)',
    )
    parser.add_argument(
        '--log-dir', default=None,
        help='logs directory (default: controller.logs_dir or <repo>/logs)',
    )
    args = parser.parse_args()

    cfg = None
    if args.config is not None:
        from .config import ConfigError, load_config, resolve_config_path
        try:
            cfg = load_config(resolve_config_path(args.config))
        except (ConfigError, json.JSONDecodeError, OSError) as e:
            print(f"elemctl.tui: config error: {e}", file=sys.stderr)
            raise SystemExit(1)
    animations_dir = resolve_runtime_path(
        args.animations,
        cfg.animations_dir if cfg is not None else None,
        DEFAULT_ANIMATIONS_PATH,
    )
    log_dir = resolve_runtime_path(
        args.log_dir,
        cfg.logs_dir if cfg is not None else None,
        DEFAULT_LOGS_PATH,
    )
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    TuiApp(
        args.socket,
        animations_dir=animations_dir,
        log_file=str(Path(log_dir) / 'tui.log'),
    ).run()


if __name__ == '__main__':
    main()
