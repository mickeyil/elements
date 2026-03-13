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
import re
import select
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from prompt_toolkit import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import (
    DynamicContainer,
    Float,
    FloatContainer,
    HSplit,
    VSplit,
    VerticalAlign,
    Window,
)
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import Button, Dialog, Frame, Label, RadioList, TextArea

from .config import (
    DEFAULT_ANIMATIONS_PATH,
    DEFAULT_CONFIG_PATH,
    DEFAULT_LOGS_PATH,
    DEFAULT_SOCKET_PATH,
    ConfigError,
    load_config,
    resolve_runtime_path,
)
from .config_edit import (
    load_config_doc,
)
from .uds_client import UdsClient
from .uds_wire import KIND_FRAME, KIND_JSON, parse_json_payload
from .version import get_runtime_version


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
_DEVICES_SENTINEL = object()
_NEWDEVICE_SENTINEL = object()

_COMMANDS = {
    'status', 'play', 'pause', 'stop', 'shutdown',
}
_DEVICE_UID_RE = re.compile(r'^[A-Za-z0-9._:-]+$')
_STRIP_ID_RE = re.compile(r'^[A-Za-z0-9_-]+$')
_PANEL_MIN_TERMINAL_WIDTH = 80
_PANEL_WIDTH = 30
_PANEL_INNER_WIDTH = _PANEL_WIDTH - 2
_STATUS_REFRESH_S = 2.0


@dataclass
class NewDeviceDialogState:
    device_type: RadioList
    device_uid: TextArea
    strip_id: TextArea
    length: TextArea
    submit_button: Button
    cancel_button: Button
    dialog: Dialog
    error_text: str = ''


@dataclass(frozen=True)
class PanelDeviceInfo:
    device_uid: str
    strip_id: str
    length: int | None
    connected: bool


@dataclass
class DevicePanelEntry:
    device_uid: str
    strip_id: str
    length: int | None
    status: str
    disconnected_at_ns: int | None = None


@dataclass(frozen=True)
class PanelSnapshotUpdate:
    devices: list[PanelDeviceInfo]


@dataclass(frozen=True)
class PanelDeviceStatusUpdate:
    device: PanelDeviceInfo


@dataclass(frozen=True)
class ControllerConnectionUpdate:
    connected: bool


class DialogButton(Button):
    """Button with explicit focused fragment styles for clearer TUI feedback."""

    def _get_text_fragments(self):
        width = (
            self.width
            - (get_cwidth(self.left_symbol) + get_cwidth(self.right_symbol))
            + (len(self.text) - get_cwidth(self.text))
        )
        text = (f"{{:^{max(0, width)}}}").format(self.text)
        focused = get_app().layout.has_focus(self)
        arrow_style = 'class:button.focused.arrow' if focused else 'class:button.arrow'
        text_style = 'class:button.focused.text' if focused else 'class:button.text'

        def handler(mouse_event) -> None:
            if (
                self.handler is not None
                and mouse_event.event_type == MouseEventType.MOUSE_UP
            ):
                self.handler()

        return [
            (arrow_style, self.left_symbol, handler),
            (text_style, text, handler),
            (arrow_style, self.right_symbol, handler),
        ]


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

    if name == 'devices':
        if len(parts) > 1:
            return None, "/devices does not take arguments"
        return _DEVICES_SENTINEL, None

    if name == 'newdevice':
        if len(parts) > 1:
            return None, "/newdevice does not take arguments"
        return _NEWDEVICE_SENTINEL, None

    if name == 'rmdevice':
        return _parse_rmdevice(parts)

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


def _parse_rmdevice(parts: list[str]) -> tuple[dict | None, str | None]:
    if len(parts) < 2:
        return None, "/rmdevice requires a device uid"
    args = parts[1].split()
    if len(args) != 1:
        return None, "/rmdevice takes exactly one device uid"
    return {'cmd': 'rmdevice', 'device_uid': args[0]}, None


def validate_device_uid(device_uid: str) -> str | None:
    if not device_uid:
        return "device uid is required"
    if not _DEVICE_UID_RE.fullmatch(device_uid):
        return "device uid may only contain letters, numbers, ., _, -, and :"
    return None


def validate_strip_id(strip_id: str) -> str | None:
    if not strip_id:
        return "strip id is required"
    if not _STRIP_ID_RE.fullmatch(strip_id):
        return "strip id may only contain letters, numbers, _ and -"
    return None


def parse_length(text: str) -> tuple[int | None, str | None]:
    if not text:
        return None, "length is required"
    try:
        length = int(text)
    except ValueError:
        return None, f"length must be an integer, got {text!r}"
    if length < 1:
        return None, f"length must be >= 1, got {length}"
    return length, None


# ------------------------------------------------------------------
# Event formatting
# ------------------------------------------------------------------

def _format_timestamp(now: datetime.datetime | None = None) -> str:
    dt = now or datetime.datetime.now()
    return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


def format_transcript_line(
    message: str,
    *,
    now: datetime.datetime | None = None,
) -> str:
    return f"[{_format_timestamp(now)}] {message}"


def _format_device_summary(dev: dict) -> str:
    uid = dev.get('device_uid', '?')
    strip = dev.get('strip', '?')
    length = dev.get('length')
    if isinstance(length, int) and not isinstance(length, bool):
        return f'{uid}: strip "{strip}" with {length} LEDs'
    return f'{uid}: strip "{strip}"'


def _normalize_length(value) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _normalize_strip_id(data: dict) -> str:
    strip = data.get('strip')
    if isinstance(strip, str) and strip:
        return strip
    strip = data.get('strip_id')
    if isinstance(strip, str) and strip:
        return strip
    return '?'


def _panel_device_info_from_dict(data: dict) -> PanelDeviceInfo | None:
    uid = data.get('device_uid')
    if not isinstance(uid, str) or not uid:
        return None
    return PanelDeviceInfo(
        device_uid=uid,
        strip_id=_normalize_strip_id(data),
        length=_normalize_length(data.get('length')),
        connected=bool(data.get('connected')),
    )


def _panel_updates_from_message(msg: dict) -> list[object]:
    msg_type = msg.get('type')
    if msg_type == 'reply' and msg.get('ok'):
        result = msg.get('result')
        if isinstance(result, dict) and result.get('event') == 'snapshot':
            devices = [
                info
                for dev in result.get('devices', [])
                if isinstance(dev, dict)
                for info in [_panel_device_info_from_dict(dev)]
                if info is not None
            ]
            return [PanelSnapshotUpdate(devices)]
        return []

    if msg_type != 'event':
        return []

    event = msg.get('event')
    if event == 'snapshot':
        devices = [
            info
            for dev in msg.get('devices', [])
            if isinstance(dev, dict)
            for info in [_panel_device_info_from_dict(dev)]
            if info is not None
        ]
        return [PanelSnapshotUpdate(devices)]

    if event == 'device_status':
        info = _panel_device_info_from_dict(msg)
        if info is not None:
            return [PanelDeviceStatusUpdate(info)]

    return []


def _decode_tui_message(kind: int, payload: bytes) -> tuple[list[str] | None, list[object]]:
    if kind == KIND_FRAME:
        return None, []

    if kind != KIND_JSON:
        return [f'ctrl: unknown message kind={kind} len={len(payload)}'], []

    try:
        msg = parse_json_payload(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return [f'ctrl: bad json message len={len(payload)}'], []

    return _format_message(msg), _panel_updates_from_message(msg)


def format_event(kind: int, payload: bytes) -> list[str] | None:
    """Format a UDS message as transcript line(s). Returns None for frames."""
    lines, _ = _decode_tui_message(kind, payload)
    return lines


def _format_message(msg: dict) -> list[str]:
    msg_type = msg.get('type')
    if msg_type == 'reply':
        rid = msg.get('id')
        if msg.get('ok'):
            result = msg.get('result', {})
            if isinstance(result, dict) and result.get('event') == 'snapshot':
                return _format_controller_event(result)
            if (
                isinstance(result, dict)
                and set(result.keys()) == {'message'}
                and isinstance(result.get('message'), str)
            ):
                return [f'ctrl: {result["message"]}']
            return [f'ctrl: reply {rid} ok {json.dumps(result, separators=(",", ":"))}']
        else:
            return [f'ctrl: reply {rid} ERROR: {msg.get("error", "?")}']

    if msg_type == 'event':
        return _format_controller_event(msg)

    return [f'ctrl: unknown message {json.dumps(msg, separators=(",", ":"))}']


def _format_controller_event(msg: dict) -> list[str]:
    event = msg.get('event')

    if event == 'snapshot':
        devices = msg.get('devices', [])
        connected = [
            _format_device_summary(dev)
            for dev in devices
            if dev.get('connected')
        ]
        session = msg.get('session')
        lines: list[str] = []
        if session:
            state = session.get('playback_state', '?')
            sid = session.get('session_id')
            t_rel = session.get('current_t_rel')
            duration = session.get('duration')
            if isinstance(t_rel, (int, float)) and isinstance(duration, (int, float)):
                lines.append(f'ctrl: {state} session={sid} t={t_rel:.2f}/{duration:.2f}s')
            else:
                lines.append(f'ctrl: {state} session={sid}')
        else:
            lines.append('ctrl: idle')

        if connected:
            lines.append('devices online:')
            lines.extend(f'  {line}' for line in connected)
        else:
            lines.append('devices online: none')
        return lines

    if event == 'state':
        state = msg.get('state', '?')
        epoch = msg.get('epoch', '?')
        sid = msg.get('session_id', '?')
        return [f'ctrl: state {state} epoch={epoch} session={sid}']

    if event == 'device_status':
        uid = msg.get('device_uid', '?')
        strip = msg.get('strip', '?')
        length = msg.get('length')
        connected = msg.get('connected')
        status = 'connected' if connected else 'disconnected'
        if isinstance(length, int) and not isinstance(length, bool):
            return [f'ctrl: device {uid} {status} ({strip}, {length} LEDs)']
        return [f'ctrl: device {uid} {status} ({strip})']

    if event == 'session_start':
        sid = msg.get('session_id', '?')
        epoch = msg.get('epoch', '?')
        duration = msg.get('duration', '?')
        strips = ', '.join(s.get('name', '?') for s in msg.get('strips', []))
        return [f'ctrl: session {sid} started epoch={epoch} duration={duration}s strips=[{strips}]']

    if event == 'loop':
        epoch = msg.get('epoch', '?')
        sid = msg.get('session_id', '?')
        return [f'ctrl: loop epoch={epoch} session={sid}']

    if event == 'error':
        return [f'ctrl: error: {msg.get("message", "?")}']

    return [f'ctrl: unknown event {json.dumps(msg, separators=(",", ":"))}']


# ------------------------------------------------------------------
# TUI application
# ------------------------------------------------------------------

class TuiApp:
    """Full-screen TUI shell for elemctl."""

    def __init__(
        self,
        socket_path: str,
        config_path: str,
        animations_dir: str = DEFAULT_ANIMATIONS_PATH,
        log_file: str | None = None,
    ):
        self._socket_path = socket_path
        self._config_path = config_path
        self._animations_dir = animations_dir
        self._animation_list: list[AnimationEntry] = []
        self._newdevice_dialog: NewDeviceDialogState | None = None
        self._client: UdsClient | None = None
        self._client_lock = threading.Lock()
        self._shutdown = threading.Event()
        self._next_id = 1
        self._log_lines: list[str] = []
        self._pending_logs: queue.Queue[str] = queue.Queue()
        self._pending_panel_updates: queue.Queue[object] = queue.Queue()
        self._device_panel: dict[str, DevicePanelEntry] = {}
        self._controller_connected = False
        self._reader_thread: threading.Thread | None = None
        self._log_fp = (
            open(log_file, 'a', encoding='utf-8', buffering=1)
            if log_file is not None
            else None
        )
        self._seed_panel_from_config()

        # prompt_toolkit widgets
        self._log_buffer = Buffer(read_only=True)
        input_control = BufferControl(buffer=Buffer(
            name='input',
            accept_handler=self._on_input,
            multiline=False,
        ))
        self._input_buffer = input_control.buffer
        self._input_control = input_control

        kb = KeyBindings()

        @kb.add('c-c')
        @kb.add('c-q')
        def _(event):
            self._exit()

        @kb.add('escape', filter=Condition(lambda: self._newdevice_dialog is not None))
        def _(event):
            self._cancel_newdevice_dialog()

        @kb.add(
            'enter',
            filter=Condition(
                lambda: self._newdevice_dialog is not None
                and get_app().layout.has_focus(self._newdevice_dialog.device_type)
            ),
            eager=True,
        )
        @kb.add(
            'tab',
            filter=Condition(
                lambda: self._newdevice_dialog is not None
                and get_app().layout.has_focus(self._newdevice_dialog.device_type)
            ),
            eager=True,
        )
        def _(event):
            self._commit_newdevice_type_selection()
            event.app.layout.focus(self._newdevice_dialog.device_uid)

        log_window = Window(
            content=BufferControl(buffer=self._log_buffer),
            wrap_lines=True,
            dont_extend_height=True,
        )
        self._transcript_body = HSplit(
            [log_window],
            align=VerticalAlign.BOTTOM,
        )
        right_panel = Frame(
            HSplit(
                [
                    Window(
                        content=FormattedTextControl(self._render_panel_rows),
                        wrap_lines=False,
                    ),
                    Window(height=1, char='─', style='class:panel.separator'),
                    Window(
                        height=4,
                        content=FormattedTextControl(self._render_panel_summary),
                        dont_extend_height=True,
                        wrap_lines=False,
                    ),
                ]
            ),
            title='devices',
            width=_PANEL_WIDTH,
        )
        self._wide_body = VSplit(
            [self._transcript_body, right_panel],
            padding=1,
        )
        main_body = HSplit(
            [
                DynamicContainer(self._select_body_container),
                Window(height=1, char='─', style='class:separator'),
                Window(height=1, content=input_control),
            ],
        )
        self._root_container = FloatContainer(
            content=Frame(main_body, title='elemctl'),
            floats=[],
            modal=True,
        )

        self._app = Application(
            layout=Layout(self._root_container, focused_element=input_control),
            key_bindings=kb,
            full_screen=True,
            before_render=self._drain_pending_ui,
            refresh_interval=_STATUS_REFRESH_S,
            style=Style.from_dict({
                'separator': '#444444',
                'panel.separator': '#21262d',
                'dialog': 'bg:#1f2430',
                'dialog.body': 'bg:#1f2430 #d8dee9',
                'dialog shadow': 'bg:#000000',
                'button': 'bg:#2f3640 #d8dee9',
                'button.text': 'bg:#2f3640 #d8dee9',
                'button.arrow': 'bg:#2f3640 #81a1c1',
                'button.focused': 'bg:#5e81ac #ffffff',
                'button.focused.text': 'bg:#5e81ac #ffffff bold',
                'button.focused.arrow': 'bg:#5e81ac #ffffff bold',
                'radio-selected': 'bg:#2b303b',
                'radio-checked': '#88c0d0',
                'dialog.body text-area': 'bg:#11151c #e5e9f0',
                'newdevice.label': 'bold #81a1c1',
                'newdevice.help': '#7f8c8d',
                'newdevice.error': 'bg:#3b1f22 #ffb4b4',
                'frame.border': '#30363d',
                'frame.label': 'bold #58a6ff',
                'panel.uid.connected': 'bold #3fb950',
                'panel.uid.offline': 'bold #f85149',
                'panel.uid.never': '#5b6573',
                'panel.meta': '#7d8590',
                'panel.length': '#8b949e',
                'panel.badge.offline': 'bold bg:#3d1212 #f85149',
                'panel.summary.label': '#8b949e',
                'panel.summary.online': '#3fb950',
                'panel.summary.offline': '#f85149',
                'panel.summary.never': '#5b6573',
                'panel.controller.online': '#3fb950',
                'panel.controller.offline': '#f85149',
                'panel.empty': '#5b6573',
            }),
        )

    def _seed_panel_from_config(self) -> None:
        try:
            doc = load_config_doc(self._config_path)
        except (ConfigError, json.JSONDecodeError, OSError):
            return

        panel: dict[str, DevicePanelEntry] = {}
        for dev in doc.get('devices', []):
            if not isinstance(dev, dict):
                continue
            uid = dev.get('device_uid')
            if not isinstance(uid, str) or not uid:
                continue
            panel[uid] = DevicePanelEntry(
                device_uid=uid,
                strip_id=_normalize_strip_id(dev),
                length=_normalize_length(dev.get('length')),
                status='never',
            )
        self._device_panel = panel

    def _select_body_container(self):
        if self._should_show_panel():
            return self._wide_body
        return self._transcript_body

    def _should_show_panel(self) -> bool:
        app = getattr(self, '_app', None)
        if app is None:
            return True
        try:
            return app.output.get_size().columns >= _PANEL_MIN_TERMINAL_WIDTH
        except OSError:
            return True

    def _enqueue_panel_update(self, update: object) -> None:
        self._pending_panel_updates.put(update)
        self._app.invalidate()

    def _apply_panel_update(self, update: object) -> None:
        if isinstance(update, ControllerConnectionUpdate):
            self._controller_connected = update.connected
            return
        if isinstance(update, PanelSnapshotUpdate):
            self._apply_snapshot_update(update.devices)
            return
        if isinstance(update, PanelDeviceStatusUpdate):
            self._apply_device_status_update(update.device)

    def _resolve_panel_device_state(
        self,
        info: PanelDeviceInfo,
        prev: DevicePanelEntry | None,
        *,
        now_ns: int | None = None,
    ) -> tuple[str, int | None]:
        if info.connected:
            return 'connected', None
        if prev is None:
            return 'never', None
        if prev.status == 'connected':
            return 'offline', time.monotonic_ns() if now_ns is None else now_ns
        if prev.status == 'offline':
            return 'offline', prev.disconnected_at_ns
        return 'never', None

    def _apply_snapshot_update(self, devices: list[PanelDeviceInfo]) -> None:
        previous = self._device_panel
        now_ns = time.monotonic_ns()
        new_panel: dict[str, DevicePanelEntry] = {}
        for info in devices:
            prev = previous.get(info.device_uid)
            status, disconnected_at_ns = self._resolve_panel_device_state(
                info,
                prev,
                now_ns=now_ns,
            )

            new_panel[info.device_uid] = DevicePanelEntry(
                device_uid=info.device_uid,
                strip_id=info.strip_id,
                length=info.length,
                status=status,
                disconnected_at_ns=disconnected_at_ns,
            )
        self._device_panel = new_panel

    def _apply_device_status_update(self, info: PanelDeviceInfo) -> None:
        prev = self._device_panel.get(info.device_uid)
        status, disconnected_at_ns = self._resolve_panel_device_state(info, prev)

        self._device_panel[info.device_uid] = DevicePanelEntry(
            device_uid=info.device_uid,
            strip_id=info.strip_id,
            length=info.length,
            status=status,
            disconnected_at_ns=disconnected_at_ns,
        )

    def _panel_truncate(self, text: str, width: int) -> str:
        if width <= 0:
            return ''
        if get_cwidth(text) <= width:
            return text
        if width == 1:
            return '…'
        out = []
        used = 0
        for ch in text:
            w = get_cwidth(ch)
            if used + w > width - 1:
                break
            out.append(ch)
            used += w
        out.append('…')
        return ''.join(out)

    def _panel_two_column_fragments(
        self,
        left_text: str,
        *,
        left_style: str,
        right_text: str = '',
        right_style: str = '',
    ) -> list[tuple[str, str]]:
        width = _PANEL_INNER_WIDTH
        right_width = get_cwidth(right_text)
        left_width = width
        if right_text:
            left_width = max(1, width - right_width - 1)
        left_rendered = self._panel_truncate(left_text, left_width)
        gap = width - get_cwidth(left_rendered) - right_width
        if right_text:
            gap = max(1, gap)
        else:
            gap = max(0, gap)
        fragments: list[tuple[str, str]] = [(left_style, left_rendered)]
        if gap:
            fragments.append(('', ' ' * gap))
        if right_text:
            fragments.append((right_style, right_text))
        return fragments

    def _format_panel_age(self, disconnected_at_ns: int | None) -> str:
        if disconnected_at_ns is None:
            return ''
        elapsed_s = max(0, int((time.monotonic_ns() - disconnected_at_ns) / 1_000_000_000))
        if elapsed_s < 60:
            return f'{elapsed_s}s'
        elapsed_m = elapsed_s // 60
        if elapsed_m < 60:
            return f'{elapsed_m}m'
        elapsed_h = elapsed_m // 60
        if elapsed_h < 24:
            return f'{elapsed_h}h'
        return f'{elapsed_h // 24}d'

    def _render_panel_rows(self):
        fragments: list[tuple[str, str]] = []
        if not self._device_panel:
            return [('class:panel.empty', 'no configured devices')]

        first = True
        for entry in self._device_panel.values():
            if not first:
                fragments.append(('', '\n'))
            first = False
            badge = ''
            uid_style = 'class:panel.uid.never'
            if entry.status == 'connected':
                uid_style = 'class:panel.uid.connected'
            elif entry.status == 'offline':
                uid_style = 'class:panel.uid.offline'
                badge = self._format_panel_age(entry.disconnected_at_ns)
            fragments.extend(
                self._panel_two_column_fragments(
                    entry.device_uid,
                    left_style=uid_style,
                    right_text=badge,
                    right_style='class:panel.badge.offline',
                )
            )
            fragments.append(('', '\n'))
            length_text = '?' if entry.length is None else str(entry.length)
            fragments.extend(
                self._panel_two_column_fragments(
                    entry.strip_id,
                    left_style='class:panel.meta',
                    right_text=length_text,
                    right_style='class:panel.length',
                )
            )
        return fragments

    def _render_panel_summary(self):
        fragments: list[tuple[str, str]] = []
        counts = {'connected': 0, 'offline': 0, 'never': 0}
        for entry in self._device_panel.values():
            counts[entry.status] += 1

        controller_style = (
            'class:panel.controller.online'
            if self._controller_connected
            else 'class:panel.controller.offline'
        )
        fragments.extend(
            self._panel_two_column_fragments(
                'controller',
                left_style='class:panel.summary.label',
                right_text='online' if self._controller_connected else 'offline',
                right_style=controller_style,
            )
        )
        fragments.append(('', '\n'))
        fragments.extend(
            self._panel_two_column_fragments(
                'online',
                left_style='class:panel.summary.online',
                right_text=str(counts['connected']),
                right_style='class:panel.summary.online',
            )
        )
        fragments.append(('', '\n'))
        fragments.extend(
            self._panel_two_column_fragments(
                'offline',
                left_style='class:panel.summary.offline',
                right_text=str(counts['offline']),
                right_style='class:panel.summary.offline',
            )
        )
        fragments.append(('', '\n'))
        fragments.extend(
            self._panel_two_column_fragments(
                'never',
                left_style='class:panel.summary.never',
                right_text=str(counts['never']),
                right_style='class:panel.summary.never',
            )
        )
        return fragments

    def run(self) -> None:
        """Connect and run the TUI. Blocks until exit."""
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        try:
            self._local_log(f'elements tui started. version: {get_runtime_version()}')
            self._reader_thread.start()
            self._app.run()
        finally:
            self._shutdown.set()
            self._clear_client()
            if self._reader_thread is not None:
                self._reader_thread.join(timeout=1.0)
            if self._log_fp is not None:
                self._log_fp.close()

    # ------------------------------------------------------------------
    # Input handling
    # ------------------------------------------------------------------

    def _on_input(self, buff: Buffer) -> None:
        text = buff.text
        if self._newdevice_dialog is not None:
            return

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

        if cmd is _DEVICES_SENTINEL:
            self._do_devices()
            return

        if cmd is _NEWDEVICE_SENTINEL:
            self._start_newdevice()
            return

        if isinstance(cmd, dict) and cmd.get('cmd') == 'load':
            self._do_load(cmd)
            return

        if isinstance(cmd, dict) and cmd.get('cmd') == 'rmdevice':
            self._do_rmdevice(cmd['device_uid'])
            return

        self._local_log(f"> {text}")
        self._next_id += 1

        client = self._get_client()
        if client is None:
            self._local_log("not connected")
            return

        try:
            client.send_cmd(cmd)
        except OSError as e:
            self._local_log(f"send failed: {e}")

    # ------------------------------------------------------------------
    # /help, /rescan and /load
    # ------------------------------------------------------------------

    def _show_help(self) -> None:
        self._local_log("commands:")
        self._local_log("  /help               show this help")
        self._local_log("  /devices            list configured devices")
        self._local_log("  /newdevice          open new device dialog")
        self._local_log("  /rmdevice UID       remove a configured device")
        self._local_log("  /status             show controller status")
        self._local_log("  /play               start playback")
        self._local_log("  /pause              pause playback")
        self._local_log("  /stop               stop playback")
        self._local_log("  /shutdown           stop the controller service")
        self._local_log("  /rescan             rescan the animations directory")
        self._local_log("  /load NAME [loop]   load an animation by name")
        self._local_log("  /load #N [loop]     load an animation by list index")
        self._local_log("  /quit, /exit        quit the TUI")

    def _load_config_doc(self) -> dict | None:
        try:
            return load_config_doc(self._config_path)
        except (ConfigError, json.JSONDecodeError, OSError) as e:
            self._local_log(f"config error: {e}")
            return None

    def _do_devices(self) -> None:
        client = self._get_client()
        if client is not None:
            cmd = {'cmd': 'status', 'id': self._next_id}
            self._next_id += 1
            try:
                client.send_cmd(cmd)
            except OSError as e:
                self._local_log(f"send failed: {e}")
            return

        doc = self._load_config_doc()
        if doc is None:
            return
        devices = doc.get('devices', [])
        if not devices:
            self._local_log(f"no configured devices in {self._config_path}")
            return
        self._local_log(f"configured devices in {self._config_path}:")
        for dev in devices:
            uid = dev.get('device_uid', '?')
            dev_type = dev.get('device_type', '?')
            strip = dev.get('strip_id', '?')
            length = dev.get('length', '?')
            device_id = dev.get('device_id', '?')
            self._local_log(
                f'  {uid}: {dev_type}, strip "{strip}", {length} LEDs (id {device_id})'
            )

    def _do_rmdevice(self, device_uid: str) -> None:
        client = self._get_client()
        if client is None:
            self._local_log("controller not connected")
            return
        cmd = {
            'cmd': 'remove_device',
            'device_uid': device_uid,
            'id': self._next_id,
        }
        self._next_id += 1
        try:
            client.send_cmd(cmd)
        except OSError as e:
            self._local_log(f"send failed: {e}")

    def _start_newdevice(self) -> None:
        if self._newdevice_dialog is not None:
            return
        if self._get_client() is None:
            self._local_log("controller not connected")
            return
        self._newdevice_dialog = self._build_newdevice_dialog()
        self._root_container.floats[:] = [Float(content=self._newdevice_dialog.dialog)]
        self._app.layout.focus(self._newdevice_dialog.device_type)
        self._app.invalidate()

    def _build_newdevice_dialog(self) -> NewDeviceDialogState:
        device_type = RadioList(
            values=[('sim', 'sim'), ('esp32', 'esp32')],
            default='esp32',
            select_on_focus=True,
        )
        device_uid = TextArea(multiline=False, wrap_lines=False)
        strip_id = TextArea(multiline=False, wrap_lines=False)
        length = TextArea(multiline=False, wrap_lines=False)
        submit_button = DialogButton('OK', handler=self._submit_newdevice_dialog)
        cancel_button = DialogButton('Cancel', handler=self._cancel_newdevice_dialog)
        device_uid.buffer.accept_handler = (
            lambda buff: self._focus_dialog_widget(strip_id)
        )
        strip_id.buffer.accept_handler = (
            lambda buff: self._focus_dialog_widget(length)
        )
        length.buffer.accept_handler = (
            lambda buff: self._focus_dialog_widget(submit_button)
        )

        error_control = FormattedTextControl(
            text=lambda: self._newdevice_dialog.error_text if self._newdevice_dialog else ' '
        )
        dialog = Dialog(
            title='New device',
            body=HSplit([
                Label(text='Device type', style='class:newdevice.label'),
                device_type,
                Label(text='Device uid', style='class:newdevice.label'),
                device_uid,
                Label(text='Strip id', style='class:newdevice.label'),
                strip_id,
                Label(text='Length', style='class:newdevice.label'),
                length,
                Window(height=1, content=error_control, style='class:newdevice.error'),
                Label(
                    text='Enter: next/select   Tab: move   Esc: cancel',
                    style='class:newdevice.help',
                ),
            ]),
            buttons=[submit_button, cancel_button],
            with_background=True,
        )
        state = NewDeviceDialogState(
            device_type=device_type,
            device_uid=device_uid,
            strip_id=strip_id,
            length=length,
            submit_button=submit_button,
            cancel_button=cancel_button,
            dialog=dialog,
        )
        return state

    def _focus_dialog_widget(self, target) -> bool:
        self._app.layout.focus(target)
        return True

    def _commit_newdevice_type_selection(self) -> None:
        state = self._newdevice_dialog
        if state is None:
            return
        idx = state.device_type._selected_index
        state.device_type.current_value = state.device_type.values[idx][0]

    def _set_dialog_error(self, message: str, field) -> None:
        state = self._newdevice_dialog
        if state is None:
            return
        state.error_text = message
        self._app.layout.focus(field)
        self._app.invalidate()

    def _cancel_newdevice_dialog(self) -> None:
        if self._newdevice_dialog is None:
            return
        self._newdevice_dialog = None
        self._root_container.floats.clear()
        self._app.layout.focus(self._input_control)
        self._app.invalidate()

    def _submit_newdevice_dialog(self) -> None:
        state = self._newdevice_dialog
        if state is None:
            return

        doc = self._load_config_doc()
        if doc is None:
            self._set_dialog_error('config error', state.device_uid)
            return
        devices = doc.get('devices', [])

        device_type = state.device_type.current_value
        device_uid = state.device_uid.text.strip()
        strip_id = state.strip_id.text.strip()
        length_text = state.length.text.strip()

        error = validate_device_uid(device_uid)
        if error is not None:
            self._set_dialog_error(error, state.device_uid)
            return
        if any(d.get('device_uid') == device_uid for d in devices if isinstance(d, dict)):
            self._set_dialog_error(
                f'device uid already exists: {device_uid}',
                state.device_uid,
            )
            return

        error = validate_strip_id(strip_id)
        if error is not None:
            self._set_dialog_error(error, state.strip_id)
            return
        if any(d.get('strip_id') == strip_id for d in devices if isinstance(d, dict)):
            self._set_dialog_error(
                f'strip id already exists: {strip_id}',
                state.strip_id,
            )
            return

        length, error = parse_length(length_text)
        if error is not None:
            self._set_dialog_error(error, state.length)
            return

        client = self._get_client()
        if client is None:
            self._set_dialog_error('controller not connected', state.device_uid)
            return

        cmd = {
            'cmd': 'add_device',
            'device_uid': device_uid,
            'device_type': device_type,
            'strip_id': strip_id,
            'length': length,
            'id': self._next_id,
        }
        self._next_id += 1
        try:
            client.send_cmd(cmd)
        except OSError as e:
            self._set_dialog_error(f'send failed: {e}', state.device_uid)
            return

        self._cancel_newdevice_dialog()

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

        client = self._get_client()
        if client is None:
            self._local_log("not connected")
            return

        try:
            client.send_cmd(wire_cmd)
        except OSError as e:
            self._local_log(f"send failed: {e}")

    # ------------------------------------------------------------------
    # Reader thread
    # ------------------------------------------------------------------

    def _reader_loop(self) -> None:
        waiting_logged = False
        while not self._shutdown.is_set():
            client = self._get_client()
            if client is None:
                try:
                    client = UdsClient(self._socket_path)
                except (ConnectionError, OSError):
                    if not waiting_logged:
                        self._enqueue_log(format_transcript_line(
                            f'waiting for controller at {self._socket_path}'
                        ))
                        waiting_logged = True
                    self._shutdown.wait(1.0)
                    continue
                self._set_client(client)
                self._enqueue_panel_update(ControllerConnectionUpdate(True))
                self._enqueue_log(format_transcript_line(
                    f'connected to {self._socket_path}'
                ))
                waiting_logged = False
                continue

            try:
                readable, _, _ = select.select([client.fileno()], [], [], 0.1)
            except (OSError, ValueError):
                if self._drop_client(client):
                    self._enqueue_panel_update(ControllerConnectionUpdate(False))
                    self._enqueue_log(format_transcript_line(
                        f'disconnected from {self._socket_path}'
                    ))
                waiting_logged = False
                continue

            if not readable:
                continue

            try:
                messages = client.recv_once()
            except (ConnectionError, OSError):
                if self._drop_client(client):
                    self._enqueue_panel_update(ControllerConnectionUpdate(False))
                    self._enqueue_log(format_transcript_line(
                        f'disconnected from {self._socket_path}'
                    ))
                waiting_logged = False
                continue

            for kind, payload in messages:
                if kind == KIND_FRAME:
                    continue
                lines, panel_updates = _decode_tui_message(kind, payload)
                for update in panel_updates:
                    self._enqueue_panel_update(update)
                if lines is not None:
                    for line in lines:
                        self._enqueue_log(format_transcript_line(line))

    def _get_client(self) -> UdsClient | None:
        with self._client_lock:
            return self._client

    def _set_client(self, client: UdsClient) -> None:
        with self._client_lock:
            self._client = client

    def _drop_client(self, client: UdsClient) -> bool:
        with self._client_lock:
            if self._client is not client:
                return False
            self._client = None
        try:
            client.close()
        except OSError:
            pass
        return True

    def _clear_client(self) -> None:
        with self._client_lock:
            client = self._client
            self._client = None
        if client is None:
            return
        try:
            client.close()
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Log pane
    # ------------------------------------------------------------------

    def _local_log(self, message: str) -> None:
        self._append_log(format_transcript_line(message))

    def _write_transcript_line(self, line: str) -> None:
        if self._log_fp is None:
            return
        self._log_fp.write(line + '\n')

    def _enqueue_log(self, line: str) -> None:
        """Thread-safe: push a log line for the UI thread to drain."""
        self._pending_logs.put(line)
        self._app.invalidate()

    def _append_log(self, line: str) -> None:
        """UI-thread only: directly append a log line and update the buffer."""
        self._log_lines.append(line)
        self._write_transcript_line(line)
        self._flush_log_buffer()

    def _drain_pending_ui(self, app) -> None:
        """Called by prompt_toolkit before each render (UI thread)."""
        while True:
            try:
                update = self._pending_panel_updates.get_nowait()
            except queue.Empty:
                break
            self._apply_panel_update(update)

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
        help=f'Config file path to read and edit (default: {DEFAULT_CONFIG_PATH})',
    )
    parser.add_argument(
        '--log-dir', default=None,
        help='logs directory (default: controller.logs_dir or <repo>/logs)',
    )
    args = parser.parse_args()

    config_path = os.path.expanduser(args.config or DEFAULT_CONFIG_PATH)
    cfg = None
    if os.path.isfile(config_path):
        try:
            cfg = load_config(config_path)
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
        config_path=config_path,
        animations_dir=animations_dir,
        log_file=str(Path(log_dir) / 'tui.log'),
    ).run()


if __name__ == '__main__':
    main()
