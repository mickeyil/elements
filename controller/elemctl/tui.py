"""Minimal TUI shell for elemctl — connects to the controller service over UDS.

Usage: python -m elemctl.tui [--socket PATH]
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import queue
import re
import select
import threading
import time
from dataclasses import dataclass, field
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
from prompt_toolkit.widgets import Button, CheckboxList, Dialog, Frame, Label, RadioList, TextArea

from .config import (
    DEFAULT_LOGS_PATH,
    DEFAULT_SOCKET_PATH,
)
from .uds_client import UdsClient
from .uds_wire import KIND_FRAME, KIND_JSON, parse_json_payload
from .version import get_runtime_version


# ------------------------------------------------------------------
# Command parsing
# ------------------------------------------------------------------

_QUIT_SENTINEL = object()
_RESCAN_SENTINEL = object()
_HELP_SENTINEL = object()
_DEVICES_SENTINEL = object()
_PROGRAMS_SENTINEL = object()
_SESSION_SENTINEL = object()
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
    pending_request_id: int | None = None


@dataclass(frozen=True)
class DeviceSnapshotInfo:
    device_uid: str
    strip_id: str
    length: int | None
    connected: bool
    device_id: int | None = None
    device_type: str | None = None


PanelDeviceInfo = DeviceSnapshotInfo


@dataclass
class DevicePanelEntry:
    device_uid: str
    strip_id: str
    length: int | None
    status: str
    disconnected_at_ns: int | None = None


@dataclass(frozen=True)
class PanelSnapshotUpdate:
    devices: list[DeviceSnapshotInfo]


@dataclass(frozen=True)
class PanelDeviceStatusUpdate:
    device: DeviceSnapshotInfo


@dataclass(frozen=True)
class DeviceCatalogEntry:
    device_id: int | None
    device_uid: str
    device_type: str | None
    strip_id: str
    length: int | None
    connected: bool


@dataclass(frozen=True)
class DeviceCatalogSnapshotUpdate:
    devices: list[DeviceCatalogEntry]


@dataclass(frozen=True)
class DeviceCatalogStatusUpdate:
    device: DeviceCatalogEntry


@dataclass(frozen=True)
class ProgramCatalogEntry:
    program_id: str
    beat: float | None
    duration: float | None
    error: str | None
    strips: list[str] | None = None


@dataclass(frozen=True)
class ProgramCatalogUpdate:
    programs: list[ProgramCatalogEntry]


@dataclass(frozen=True)
class SessionStripTargetEntry:
    device_id: int | None
    device_uid: str
    length: int | None


@dataclass(frozen=True)
class SessionStripEntry:
    name: str
    length: int | None
    targets: list[SessionStripTargetEntry] = field(default_factory=list)


@dataclass(frozen=True)
class SessionInfo:
    session_id: int | None = None
    playback_state: str | None = None
    epoch: int | None = None
    current_t_rel: float | None = None
    duration: float | None = None
    safe_intervals: list[tuple[float, float]] | None = None
    strips: list[SessionStripEntry] | None = None


@dataclass(frozen=True)
class SessionStateUpdate:
    mode: str
    session: SessionInfo | None = None


@dataclass(frozen=True)
class CommandReplyUpdate:
    reply_id: int | None
    ok: bool
    error: str | None


@dataclass(frozen=True)
class ControllerConnectionUpdate:
    connected: bool


@dataclass
class DeviceManagerDialogState:
    device_list: RadioList | None
    edit_button: Button | None
    remove_button: Button | None
    add_button: Button | None
    close_button: Button
    dialog: Dialog


@dataclass
class DeviceEditDialogState:
    target_device_uid: str
    device_type: str | None
    device_uid: TextArea
    strip_id: TextArea
    length: TextArea
    submit_button: Button
    cancel_button: Button
    dialog: Dialog
    error_text: str = ''
    pending_request_id: int | None = None


@dataclass
class DeviceRemoveDialogState:
    device_uid: str
    confirm_button: Button
    cancel_button: Button
    dialog: Dialog
    error_text: str = ''
    pending_request_id: int | None = None


@dataclass
class ProgramManagerDialogState:
    program_list: RadioList | None
    load_button: Button | None
    loop_toggle_button: Button | None
    publish_button: Button
    rescan_button: Button
    close_button: Button
    dialog: Dialog
    error_text: str = ''
    pending_request_id: int | None = None
    loop_enabled: bool = False


@dataclass
class ProgramPublishDialogState:
    path: TextArea
    program_id: TextArea
    submit_button: Button
    cancel_button: Button
    dialog: Dialog
    return_selected_program_id: str | None = None
    error_text: str = ''
    pending_request_id: int | None = None


@dataclass
class ProgramTargetSection:
    strip_id: str
    target_list: CheckboxList | None


@dataclass
class ProgramTargetDialogState:
    program_id: str
    strip_sections: list[ProgramTargetSection]
    load_button: Button | None
    loop_toggle_button: Button
    back_button: Button
    close_button: Button
    dialog: Dialog
    error_text: str = ''
    pending_request_id: int | None = None
    loop_enabled: bool = False

    @property
    def strip_ids(self) -> list[str]:
        return [section.strip_id for section in self.strip_sections]

    @property
    def strip_id(self) -> str | None:
        if len(self.strip_sections) != 1:
            return None
        return self.strip_sections[0].strip_id

    @property
    def target_list(self) -> CheckboxList | None:
        if len(self.strip_sections) != 1:
            return None
        return self.strip_sections[0].target_list


@dataclass
class SessionManagerDialogState:
    mode: str
    play_button: Button | None
    pause_button: Button | None
    stop_button: Button | None
    seek_button: Button | None
    programs_button: Button | None
    close_button: Button
    dialog: Dialog
    error_text: str = ''
    pending_request_id: int | None = None


@dataclass
class SessionSeekDialogState:
    t_rel: TextArea
    submit_button: Button
    cancel_button: Button
    dialog: Dialog
    error_text: str = ''
    pending_request_id: int | None = None


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

    if name == 'programs':
        if len(parts) > 1:
            return None, "/programs does not take arguments"
        return _PROGRAMS_SENTINEL, None

    if name == 'session':
        if len(parts) > 1:
            return None, "/session does not take arguments"
        return _SESSION_SENTINEL, None

    if name == 'newdevice':
        if len(parts) > 1:
            return None, "/newdevice does not take arguments"
        return _NEWDEVICE_SENTINEL, None

    if name == 'rmdevice':
        return _parse_rmdevice(parts)

    if name == 'publish':
        return _parse_publish(parts, next_id)

    if name == 'scene':
        return _parse_scene(parts, next_id)

    if name == 'load':
        return _parse_load(parts, next_id)

    if name == 'seek':
        return _parse_seek(parts, next_id)

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


def _parse_publish(parts: list[str], next_id: int) -> tuple[dict | None, str | None]:
    if len(parts) < 2:
        return None, "/publish requires a file path"

    args = parts[1].split()
    if len(args) == 1:
        path = args[0]
        program_id = Path(path).stem
    elif len(args) == 3 and args[1].lower() == 'as':
        path = args[0]
        program_id = args[2]
    else:
        return None, "/publish usage: /publish PATH [as NAME]"

    if not program_id:
        return None, f"/publish: could not derive program id from {path!r}"

    return {
        'cmd': 'publish',
        'path': path,
        'program_id': program_id,
        'id': next_id,
    }, None


def _parse_scene(parts: list[str], next_id: int) -> tuple[dict | None, str | None]:
    if len(parts) < 2:
        return None, "/scene requires a file path"
    args = parts[1].split()
    if len(args) != 1:
        return None, "/scene usage: /scene PATH"
    return {
        'cmd': 'scene',
        'path': args[0],
        'id': next_id,
    }, None


def _parse_rmdevice(parts: list[str]) -> tuple[dict | None, str | None]:
    if len(parts) < 2:
        return None, "/rmdevice requires a device uid"
    args = parts[1].split()
    if len(args) != 1:
        return None, "/rmdevice takes exactly one device uid"
    return {'cmd': 'rmdevice', 'device_uid': args[0]}, None


def _parse_seek(parts: list[str], next_id: int) -> tuple[dict | None, str | None]:
    if len(parts) < 2:
        return None, "/seek requires a time in seconds"
    args = parts[1].split()
    if len(args) != 1:
        return None, "/seek takes exactly one time value"
    try:
        t_rel = float(args[0])
    except ValueError:
        return None, f"/seek: invalid time: {args[0]}"
    if not math.isfinite(t_rel):
        return None, "/seek: time must be finite"
    if t_rel < 0:
        return None, f"/seek: time must be >= 0, got {t_rel}"
    return {'cmd': 'seek', 't_rel': t_rel, 'id': next_id}, None


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


def parse_t_rel(text: str) -> tuple[float | None, str | None]:
    if not text:
        return None, "time is required"
    try:
        t_rel = float(text)
    except ValueError:
        return None, f"time must be a number, got {text!r}"
    if not math.isfinite(t_rel):
        return None, "time must be finite"
    if t_rel < 0:
        return None, f"time must be >= 0, got {t_rel}"
    return t_rel, None


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


def _normalize_device_id(value) -> int | None:
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


def _normalize_device_type(data: dict) -> str | None:
    device_type = data.get('device_type')
    if isinstance(device_type, str) and device_type:
        return device_type
    return None


def _device_snapshot_info_from_dict(data: dict) -> DeviceSnapshotInfo | None:
    uid = data.get('device_uid')
    if not isinstance(uid, str) or not uid:
        return None
    return DeviceSnapshotInfo(
        device_id=_normalize_device_id(data.get('device_id')),
        device_uid=uid,
        device_type=_normalize_device_type(data),
        strip_id=_normalize_strip_id(data),
        length=_normalize_length(data.get('length')),
        connected=bool(data.get('connected')),
    )


def _device_catalog_entry_from_dict(data: dict) -> DeviceCatalogEntry | None:
    snapshot = _device_snapshot_info_from_dict(data)
    if snapshot is None:
        return None
    return DeviceCatalogEntry(
        device_id=snapshot.device_id,
        device_uid=snapshot.device_uid,
        device_type=snapshot.device_type,
        strip_id=snapshot.strip_id,
        length=snapshot.length,
        connected=snapshot.connected,
    )


def _panel_device_info_from_dict(data: dict) -> PanelDeviceInfo | None:
    snapshot = _device_snapshot_info_from_dict(data)
    if snapshot is None:
        return None
    return PanelDeviceInfo(
        device_id=snapshot.device_id,
        device_uid=snapshot.device_uid,
        device_type=snapshot.device_type,
        strip_id=snapshot.strip_id,
        length=snapshot.length,
        connected=snapshot.connected,
    )


def _normalize_optional_float(value) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _program_catalog_entry_from_dict(data: dict) -> ProgramCatalogEntry | None:
    program_id = data.get('program_id')
    if not isinstance(program_id, str) or not program_id:
        return None
    error = data.get('error')
    if error is not None and not isinstance(error, str):
        error = str(error)
    strips = data.get('strips')
    strip_names: list[str] | None = None
    if isinstance(strips, list):
        parsed = [item for item in strips if isinstance(item, str) and item]
        if len(parsed) == len(strips):
            strip_names = parsed
    return ProgramCatalogEntry(
        program_id=program_id,
        beat=_normalize_optional_float(data.get('beat')),
        duration=_normalize_optional_float(data.get('duration')),
        error=error,
        strips=strip_names,
    )


def _program_catalog_from_message(msg: dict) -> list[ProgramCatalogEntry]:
    programs = msg.get('programs')
    if not isinstance(programs, list):
        return []
    return [
        entry
        for item in programs
        if isinstance(item, dict)
        for entry in [_program_catalog_entry_from_dict(item)]
        if entry is not None
    ]


def _device_catalog_from_message(msg: dict) -> list[DeviceCatalogEntry]:
    devices = msg.get('devices')
    if not isinstance(devices, list):
        return []
    return [
        entry
        for item in devices
        if isinstance(item, dict)
        for entry in [_device_catalog_entry_from_dict(item)]
        if entry is not None
    ]


def _session_strip_from_dict(data: dict) -> SessionStripEntry | None:
    name = data.get('name')
    if not isinstance(name, str) or not name:
        return None
    raw_targets = data.get('targets')
    targets: list[SessionStripTargetEntry] = []
    if isinstance(raw_targets, list):
        for item in raw_targets:
            if not isinstance(item, dict):
                continue
            device_uid = item.get('device_uid')
            if not isinstance(device_uid, str) or not device_uid:
                continue
            targets.append(SessionStripTargetEntry(
                device_id=_normalize_length(item.get('device_id')),
                device_uid=device_uid,
                length=_normalize_length(item.get('length')),
            ))
    return SessionStripEntry(
        name=name,
        length=_normalize_length(data.get('length')),
        targets=targets,
    )


def _safe_intervals_from_message(msg: dict) -> list[tuple[float, float]] | None:
    raw = msg.get('safe_intervals')
    if not isinstance(raw, list):
        return None
    intervals: list[tuple[float, float]] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        lo = _normalize_optional_float(item[0])
        hi = _normalize_optional_float(item[1])
        if lo is None or hi is None:
            continue
        intervals.append((lo, hi))
    return intervals


def _session_strips_from_message(msg: dict) -> list[SessionStripEntry] | None:
    strips = msg.get('strips')
    if not isinstance(strips, list):
        return None
    return [
        entry
        for item in strips
        if isinstance(item, dict)
        for entry in [_session_strip_from_dict(item)]
        if entry is not None
    ]


def _session_info_from_dict(data: dict) -> SessionInfo:
    return SessionInfo(
        session_id=_normalize_length(data.get('session_id')),
        playback_state=data.get('playback_state')
        if isinstance(data.get('playback_state'), str)
        else None,
        epoch=_normalize_length(data.get('epoch')),
        current_t_rel=_normalize_optional_float(data.get('current_t_rel')),
        duration=_normalize_optional_float(data.get('duration')),
        safe_intervals=_safe_intervals_from_message(data),
        strips=_session_strips_from_message(data),
    )


def _session_update_from_message(msg: dict) -> SessionStateUpdate | None:
    msg_type = msg.get('type')
    if msg_type == 'reply' and msg.get('ok'):
        result = msg.get('result')
        if isinstance(result, dict) and result.get('event') == 'snapshot':
            session = result.get('session')
            if session is None:
                return SessionStateUpdate(mode='clear')
            if isinstance(session, dict):
                return SessionStateUpdate(mode='replace', session=_session_info_from_dict(session))
        return None

    if msg_type != 'event':
        return None

    event = msg.get('event')
    if event == 'snapshot':
        session = msg.get('session')
        if session is None:
            return SessionStateUpdate(mode='clear')
        if isinstance(session, dict):
            return SessionStateUpdate(mode='replace', session=_session_info_from_dict(session))
        return SessionStateUpdate(mode='clear')

    if event == 'session_start':
        return SessionStateUpdate(
            mode='replace',
            session=SessionInfo(
                session_id=_normalize_length(msg.get('session_id')),
                playback_state='loaded',
                epoch=_normalize_length(msg.get('epoch')),
                current_t_rel=0.0,
                duration=_normalize_optional_float(msg.get('duration')),
                safe_intervals=_safe_intervals_from_message(msg),
                strips=_session_strips_from_message(msg),
            ),
        )

    if event == 'state':
        return SessionStateUpdate(
            mode='merge',
            session=SessionInfo(
                session_id=_normalize_length(msg.get('session_id')),
                playback_state=msg.get('state') if isinstance(msg.get('state'), str) else None,
                epoch=_normalize_length(msg.get('epoch')),
            ),
        )

    if event == 'loop':
        return SessionStateUpdate(
            mode='merge',
            session=SessionInfo(
                session_id=_normalize_length(msg.get('session_id')),
                playback_state='playing',
                epoch=_normalize_length(msg.get('epoch')),
                current_t_rel=0.0,
            ),
        )

    return None


def _format_program_catalog(programs: list[dict]) -> list[str]:
    if not programs:
        return ['no programs in library']

    lines = [f'{len(programs)} program(s) in library:']
    for i, program in enumerate(programs, 1):
        program_id = program.get('program_id', '?')
        error = program.get('error')
        if error:
            lines.append(f'  #{i}  {program_id:<16s} ERROR: {error}')
            continue
        lines.append(
            '  '
            + f'#{i}  {program_id:<16s} '
            + f'beat={program.get("beat")} duration={program.get("duration")}'
        )
    return lines


def _format_published_program(program: dict) -> list[str]:
    program_id = program.get('program_id', '?')
    error = program.get('error')
    if error:
        return [f'published program {program_id} ERROR: {error}']
    return [
        'published program '
        + f'{program_id} beat={program.get("beat")} duration={program.get("duration")}'
    ]


def _reply_update_from_message(msg: dict) -> CommandReplyUpdate | None:
    if msg.get('type') != 'reply':
        return None
    reply_id = msg.get('id')
    if not isinstance(reply_id, int):
        reply_id = None
    if msg.get('ok'):
        return CommandReplyUpdate(reply_id=reply_id, ok=True, error=None)
    error = msg.get('error', '?')
    if not isinstance(error, str):
        error = str(error)
    return CommandReplyUpdate(reply_id=reply_id, ok=False, error=error)


def _panel_updates_from_message(msg: dict) -> list[object]:
    session_update = _session_update_from_message(msg)
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
            catalog = _device_catalog_from_message(result)
            programs = _program_catalog_from_message(result)
            return [
                PanelSnapshotUpdate(devices),
                DeviceCatalogSnapshotUpdate(catalog),
                ProgramCatalogUpdate(programs),
                *([session_update] if session_update is not None else []),
            ]
        return [session_update] if session_update is not None else []

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
        catalog = _device_catalog_from_message(msg)
        programs = _program_catalog_from_message(msg)
        return [
            PanelSnapshotUpdate(devices),
            DeviceCatalogSnapshotUpdate(catalog),
            ProgramCatalogUpdate(programs),
            *([session_update] if session_update is not None else []),
        ]

    if event == 'device_status':
        info = _panel_device_info_from_dict(msg)
        if info is not None:
            catalog_entry = _device_catalog_entry_from_dict(msg)
            if catalog_entry is not None:
                return [PanelDeviceStatusUpdate(info), DeviceCatalogStatusUpdate(catalog_entry)]
            return [PanelDeviceStatusUpdate(info)]

    if event == 'programs_updated':
        return [ProgramCatalogUpdate(_program_catalog_from_message(msg))]

    if session_update is not None:
        return [session_update]

    return []


def _decode_tui_message(kind: int, payload: bytes) -> tuple[list[str] | None, list[object]]:
    if kind == KIND_FRAME:
        return None, []

    if kind != KIND_JSON:
        return [f'unknown message kind={kind} len={len(payload)}'], []

    try:
        msg = parse_json_payload(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return [f'bad json message len={len(payload)}'], []

    updates = _panel_updates_from_message(msg)
    reply_update = _reply_update_from_message(msg)
    if reply_update is not None:
        updates = [*updates, reply_update]
    return _format_message(msg), updates


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
            if isinstance(result, dict) and isinstance(result.get('programs'), list):
                return _format_program_catalog(result['programs'])
            if isinstance(result, dict) and isinstance(result.get('program'), dict):
                return _format_published_program(result['program'])
            if (
                isinstance(result, dict)
                and set(result.keys()) == {'message'}
                and isinstance(result.get('message'), str)
            ):
                return [result["message"]]
            return [f'reply {rid} ok {json.dumps(result, separators=(",", ":"))}']
        else:
            return [f'reply {rid} ERROR: {msg.get("error", "?")}']

    if msg_type == 'event':
        return _format_controller_event(msg)

    return [f'unknown message {json.dumps(msg, separators=(",", ":"))}']


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
                lines.append(f'{state} session={sid} t={t_rel:.2f}/{duration:.2f}s')
            else:
                lines.append(f'{state} session={sid}')
        else:
            lines.append('controller is idle')

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
        return [f'state {state} epoch={epoch} session={sid}']

    if event == 'device_status':
        uid = msg.get('device_uid', '?')
        strip = msg.get('strip', '?')
        length = msg.get('length')
        connected = msg.get('connected')
        status = 'connected' if connected else 'disconnected'
        if isinstance(length, int) and not isinstance(length, bool):
            return [f'device {uid} {status} ({strip}, {length} LEDs)']
        return [f'device {uid} {status} ({strip})']

    if event == 'session_start':
        sid = msg.get('session_id', '?')
        epoch = msg.get('epoch', '?')
        duration = msg.get('duration', '?')
        strips = ', '.join(s.get('name', '?') for s in msg.get('strips', []))
        return [f'session {sid} started epoch={epoch} duration={duration}s strips=[{strips}]']

    if event == 'loop':
        epoch = msg.get('epoch', '?')
        sid = msg.get('session_id', '?')
        return [f'loop epoch={epoch} session={sid}']

    if event == 'error':
        return [f'error: {msg.get("message", "?")}']

    if event == 'programs_updated':
        return []

    return [f'unknown event {json.dumps(msg, separators=(",", ":"))}']


# ------------------------------------------------------------------
# TUI application
# ------------------------------------------------------------------

class TuiApp:
    """Full-screen TUI shell for elemctl."""

    def __init__(
        self,
        socket_path: str,
        log_file: str | None = None,
    ):
        self._socket_path = socket_path
        self._newdevice_dialog: NewDeviceDialogState | None = None
        self._active_modal: object | None = None
        self._client: UdsClient | None = None
        self._client_lock = threading.Lock()
        self._shutdown = threading.Event()
        self._next_id = 1
        self._log_lines: list[str] = []
        self._pending_logs: queue.Queue[str] = queue.Queue()
        self._pending_panel_updates: queue.Queue[object] = queue.Queue()
        self._device_panel: dict[str, DevicePanelEntry] = {}
        self._device_catalog: list[DeviceCatalogEntry] = []
        self._device_catalog_ready = False
        self._program_catalog: list[ProgramCatalogEntry] = []
        self._program_catalog_ready = False
        self._session: SessionInfo | None = None
        self._controller_connected = False
        self._controller_disconnected_at_ns = time.monotonic_ns()
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
        self._input_control = input_control
        self._input_prompt = FormattedTextControl('> ')

        kb = KeyBindings()

        @kb.add('c-c')
        @kb.add('c-q')
        def _(event):
            self._exit()

        @kb.add('escape', filter=Condition(lambda: self._active_modal is not None))
        def _(event):
            self._cancel_active_modal()

        @kb.add(
            'enter',
            filter=Condition(
                lambda: isinstance(self._active_modal, NewDeviceDialogState)
                and get_app().layout.has_focus(self._newdevice_dialog.device_type)
            ),
            eager=True,
        )
        @kb.add(
            'tab',
            filter=Condition(
                lambda: isinstance(self._active_modal, NewDeviceDialogState)
                and get_app().layout.has_focus(self._newdevice_dialog.device_type)
            ),
            eager=True,
        )
        def _(event):
            self._commit_newdevice_type_selection()
            event.app.layout.focus(self._newdevice_dialog.device_uid)

        @kb.add(
            'enter',
            filter=Condition(
                lambda: isinstance(self._active_modal, DeviceManagerDialogState)
                and self._active_modal.device_list is not None
                and get_app().layout.has_focus(self._active_modal.device_list)
            ),
            eager=True,
        )
        def _(event):
            self._start_device_edit()

        @kb.add(
            'enter',
            filter=Condition(
                lambda: isinstance(self._active_modal, ProgramManagerDialogState)
                and self._active_modal.program_list is not None
                and get_app().layout.has_focus(self._active_modal.program_list)
            ),
            eager=True,
        )
        def _(event):
            self._submit_program_load()

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
                        height=2,
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
        input_row = VSplit(
            [
                Window(
                    width=2,
                    dont_extend_width=True,
                    content=self._input_prompt,
                ),
                Window(height=1, content=input_control),
            ],
        )
        main_body = HSplit(
            [
                DynamicContainer(self._select_body_container),
                Window(height=1, char='─', style='class:separator'),
                input_row,
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
                'panel.uid.connected': 'bold #00ff5f',
                'panel.uid.offline': 'bold #f85149',
                'panel.meta': '#7d8590',
                'panel.length': '#8b949e',
                'panel.badge.offline': 'bold bg:#3d1212 #f85149',
                'panel.summary.label': '#8b949e',
                'panel.summary.online': 'bold #00ff5f',
                'panel.controller.online': 'bold #00ff5f',
                'panel.controller.offline': 'bold #f85149',
            }),
        )

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
            if update.connected:
                self._controller_disconnected_at_ns = None
            elif self._controller_connected:
                self._controller_disconnected_at_ns = time.monotonic_ns()
                self._device_catalog_ready = False
                self._program_catalog_ready = False
                self._session = None
                self._refresh_session_dialog()
            self._controller_connected = update.connected
            return
        if isinstance(update, PanelSnapshotUpdate):
            self._apply_snapshot_update(update.devices)
            return
        if isinstance(update, PanelDeviceStatusUpdate):
            self._apply_device_status_update(update.device)
            return
        if isinstance(update, DeviceCatalogSnapshotUpdate):
            self._apply_device_catalog_snapshot(update.devices)
            return
        if isinstance(update, DeviceCatalogStatusUpdate):
            self._apply_device_catalog_status(update.device)
            return
        if isinstance(update, ProgramCatalogUpdate):
            self._apply_program_catalog_update(update.programs)
            return
        if isinstance(update, SessionStateUpdate):
            self._apply_session_update(update)
            return
        if isinstance(update, CommandReplyUpdate):
            self._apply_command_reply_update(update)

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
            return 'configured', None
        if prev.status == 'connected':
            return 'offline', time.monotonic_ns() if now_ns is None else now_ns
        if prev.status == 'offline':
            return 'offline', prev.disconnected_at_ns
        return 'configured', None

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

    def _apply_device_catalog_snapshot(self, devices: list[DeviceCatalogEntry]) -> None:
        self._device_catalog = list(devices)
        self._device_catalog_ready = True
        self._refresh_device_manager_dialog()
        self._refresh_program_target_dialog()

    def _apply_device_catalog_status(self, info: DeviceCatalogEntry) -> None:
        updated: list[DeviceCatalogEntry] = []
        found = False
        for entry in self._device_catalog:
            if entry.device_uid != info.device_uid:
                updated.append(entry)
                continue
            found = True
            updated.append(DeviceCatalogEntry(
                device_id=info.device_id if info.device_id is not None else entry.device_id,
                device_uid=info.device_uid,
                device_type=info.device_type if info.device_type is not None else entry.device_type,
                strip_id=info.strip_id,
                length=info.length,
                connected=info.connected,
            ))
        if not found:
            updated.append(info)
        self._device_catalog = updated
        self._refresh_device_manager_dialog()
        self._refresh_program_target_dialog()

    def _apply_program_catalog_update(self, programs: list[ProgramCatalogEntry]) -> None:
        self._program_catalog = list(programs)
        self._program_catalog_ready = True
        self._refresh_program_manager_dialog()

    @staticmethod
    def _merge_session_info(
        existing: SessionInfo | None,
        incoming: SessionInfo,
    ) -> SessionInfo:
        base = existing or SessionInfo()
        if (
            existing is not None
            and incoming.session_id is not None
            and existing.session_id is not None
            and existing.session_id != incoming.session_id
        ):
            base = SessionInfo()
        return SessionInfo(
            session_id=(
                incoming.session_id
                if incoming.session_id is not None
                else base.session_id
            ),
            playback_state=(
                incoming.playback_state
                if incoming.playback_state is not None
                else base.playback_state
            ),
            epoch=incoming.epoch if incoming.epoch is not None else base.epoch,
            current_t_rel=(
                incoming.current_t_rel
                if incoming.current_t_rel is not None
                else base.current_t_rel
            ),
            duration=(
                incoming.duration
                if incoming.duration is not None
                else base.duration
            ),
            safe_intervals=(
                incoming.safe_intervals
                if incoming.safe_intervals is not None
                else base.safe_intervals
            ),
            strips=incoming.strips if incoming.strips is not None else base.strips,
        )

    def _apply_session_update(self, update: SessionStateUpdate) -> None:
        if update.mode == 'clear':
            self._session = None
            self._refresh_session_dialog()
            return
        if update.session is None:
            return
        if update.mode == 'replace':
            self._session = update.session
        else:
            self._session = self._merge_session_info(self._session, update.session)
        self._refresh_session_dialog()

    def _apply_command_reply_update(self, update: CommandReplyUpdate) -> None:
        modal = self._active_modal
        if isinstance(modal, NewDeviceDialogState):
            if modal.pending_request_id != update.reply_id:
                return
            modal.pending_request_id = None
            if update.ok:
                self._close_modal()
            else:
                modal.error_text = update.error or 'unknown error'
                self._app.invalidate()
            return
        if isinstance(modal, ProgramManagerDialogState):
            if modal.pending_request_id != update.reply_id:
                return
            modal.pending_request_id = None
            if update.ok:
                modal.error_text = ''
                self._app.invalidate()
            else:
                modal.error_text = update.error or 'unknown error'
                self._app.invalidate()
            return
        if isinstance(modal, ProgramTargetDialogState):
            if modal.pending_request_id != update.reply_id:
                return
            modal.pending_request_id = None
            if update.ok:
                self._open_program_manager(
                    selected_program_id=modal.program_id,
                    loop_enabled=modal.loop_enabled,
                )
            else:
                modal.error_text = update.error or 'unknown error'
                self._app.invalidate()
            return
        if isinstance(modal, ProgramPublishDialogState):
            if modal.pending_request_id != update.reply_id:
                return
            modal.pending_request_id = None
            if update.ok:
                selected_program_id = self._program_id_from_publish_fields(
                    modal.path.text.strip(),
                    modal.program_id.text.strip(),
                )
                self._open_program_manager(selected_program_id=selected_program_id)
            else:
                modal.error_text = update.error or 'unknown error'
                self._app.invalidate()
            return
        if isinstance(modal, SessionManagerDialogState):
            if modal.pending_request_id != update.reply_id:
                return
            modal.pending_request_id = None
            if update.ok:
                modal.error_text = ''
                self._app.invalidate()
            else:
                modal.error_text = update.error or 'unknown error'
                self._app.invalidate()
            return
        if isinstance(modal, SessionSeekDialogState):
            if modal.pending_request_id != update.reply_id:
                return
            modal.pending_request_id = None
            if update.ok:
                self._open_session_manager()
            else:
                modal.error_text = update.error or 'unknown error'
                self._app.invalidate()
            return
        if isinstance(modal, DeviceEditDialogState):
            if modal.pending_request_id != update.reply_id:
                return
            modal.pending_request_id = None
            if update.ok:
                self._close_modal()
            else:
                modal.error_text = update.error or 'unknown error'
                self._app.invalidate()
            return
        if isinstance(modal, DeviceRemoveDialogState):
            if modal.pending_request_id != update.reply_id:
                return
            modal.pending_request_id = None
            if update.ok:
                self._close_modal()
            else:
                modal.error_text = update.error or 'unknown error'
                self._app.invalidate()

    def _set_modal(self, state: object, focus=None) -> None:
        self._active_modal = state
        self._newdevice_dialog = state if isinstance(state, NewDeviceDialogState) else None
        dialog = getattr(state, 'dialog')
        self._root_container.floats[:] = [Float(content=dialog)]
        if focus is not None:
            self._app.layout.focus(focus)
        self._app.invalidate()

    def _close_modal(self) -> None:
        self._active_modal = None
        self._newdevice_dialog = None
        self._root_container.floats.clear()
        self._app.layout.focus(self._input_control)
        self._app.invalidate()

    def _cancel_active_modal(self) -> None:
        if isinstance(self._active_modal, ProgramPublishDialogState):
            self._cancel_program_publish_dialog()
            return
        if isinstance(self._active_modal, SessionSeekDialogState):
            self._cancel_session_seek_dialog()
            return
        self._close_modal()

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
            return 'now'
        if elapsed_s < 3600:
            return f'{elapsed_s // 60}m'
        if elapsed_s < 86400:
            return f'{elapsed_s // 3600}h'
        return f'{elapsed_s // 86400}d'

    def _format_panel_age_badge(self, disconnected_at_ns: int | None) -> str:
        age = self._format_panel_age(disconnected_at_ns)
        if not age:
            return ''
        return f' {age} '

    def _render_panel_rows(self):
        fragments: list[tuple[str, str]] = []
        if not self._device_panel:
            return []

        visible_entries = [
            entry for entry in self._device_panel.values()
            if entry.status == 'offline'
        ]
        visible_entries.extend(
            entry for entry in self._device_panel.values()
            if entry.status == 'connected'
        )
        if not visible_entries:
            return []

        first = True
        for entry in visible_entries:
            if not first:
                fragments.append(('', '\n'))
            first = False
            badge = ''
            if entry.status == 'connected':
                uid_style = 'class:panel.uid.connected'
            else:
                uid_style = 'class:panel.uid.offline'
                badge = self._format_panel_age_badge(entry.disconnected_at_ns)
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
                    right_text=f'{length_text} ',
                    right_style='class:panel.length',
                )
            )
        return fragments

    def _render_panel_summary(self):
        fragments: list[tuple[str, str]] = []
        counts = {'connected': 0}
        for entry in self._device_panel.values():
            if entry.status == 'connected':
                counts['connected'] += 1

        controller_style = (
            'class:panel.controller.online'
            if self._controller_connected
            else 'class:panel.controller.offline'
        )
        fragments.extend(
            self._panel_two_column_fragments(
                'controller',
                left_style=controller_style,
                right_text='' if self._controller_connected else self._format_panel_age_badge(
                    self._controller_disconnected_at_ns
                ),
                right_style='class:panel.badge.offline',
            )
        )
        fragments.append(('', '\n'))
        fragments.extend(
            self._panel_two_column_fragments(
                'devices',
                left_style='class:panel.summary.label',
                right_text=str(counts['connected']),
                right_style='class:panel.summary.online',
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
        if self._active_modal is not None:
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

        if cmd is _PROGRAMS_SENTINEL:
            self._do_programs()
            return

        if cmd is _SESSION_SENTINEL:
            self._do_session()
            return

        if cmd is _NEWDEVICE_SENTINEL:
            self._start_newdevice()
            return

        if isinstance(cmd, dict) and cmd.get('cmd') == 'publish':
            self._do_publish(cmd)
            return

        if isinstance(cmd, dict) and cmd.get('cmd') == 'scene':
            self._do_scene(cmd)
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
        self._local_log("  /devices            manage configured devices")
        self._local_log("  /programs           manage programs in the controller library")
        self._local_log("  /scene FILE         load a scene JSON file")
        self._local_log("  /session            manage live playback session")
        self._local_log("  /newdevice          open new device dialog")
        self._local_log("  /rmdevice UID       remove a configured device")
        self._local_log("  /status             show controller status")
        self._local_log("  /play               start playback")
        self._local_log("  /pause              pause playback")
        self._local_log("  /stop               stop playback")
        self._local_log("  /seek SECONDS       seek playback time")
        self._local_log("  /shutdown           stop the controller service")
        self._local_log("  /rescan             rescan the controller program library")
        self._local_log("  /publish FILE [as NAME]  publish a local file to the controller library")
        self._local_log("  /load NAME [loop]   load a program by name")
        self._local_log("  /load #N [loop]     load a program by list index")
        self._local_log("  /quit, /exit        quit the TUI")

    def _do_devices(self) -> None:
        client = self._get_client()
        if client is None:
            self._local_log("controller not connected")
            return
        if self._active_modal is not None:
            return
        if not self._device_catalog_ready:
            self._local_log("device list not available yet")
            return
        self._open_device_manager()

    def _do_programs(self) -> None:
        client = self._get_client()
        if client is None:
            self._local_log("controller not connected")
            return
        if self._active_modal is not None:
            return
        if not self._program_catalog_ready:
            self._local_log("program list not available yet")
            return
        self._open_program_manager()

    def _do_session(self) -> None:
        client = self._get_client()
        if client is None:
            self._local_log("controller not connected")
            return
        if self._active_modal is not None:
            return
        if not self._device_catalog_ready:
            self._local_log("session status not available yet")
            return
        self._open_session_manager()

    def _do_scene(self, cmd: dict) -> None:
        path = cmd.get('path')
        if not isinstance(path, str) or not path:
            self._local_log("scene path is required")
            return

        if self._get_client() is None:
            self._local_log("not connected")
            return

        source, error = self._read_publish_source(path)
        if error is not None:
            self._local_log(error.replace('cannot read ', 'cannot read scene ', 1))
            return

        try:
            payload = json.loads(source)
        except json.JSONDecodeError as e:
            self._local_log(f'invalid scene JSON in {path}: {e.msg}')
            return

        if not isinstance(payload, dict):
            self._local_log('scene file must contain a JSON object')
            return

        self._local_log(f"> /scene {path}")
        _cmd_id, error = self._send_controller_cmd({
            **payload,
            'cmd': 'load_scene',
        })
        if error is not None:
            self._local_log(error)

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
        if self._active_modal is not None:
            return
        if self._get_client() is None:
            self._local_log("controller not connected")
            return
        self._open_newdevice_dialog()

    def _open_newdevice_dialog(self) -> None:
        state = self._build_newdevice_dialog()
        self._set_modal(state, focus=state.device_type)

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
        self._close_modal()

    def _submit_newdevice_dialog(self) -> None:
        state = self._newdevice_dialog
        if state is None:
            return
        if state.pending_request_id is not None:
            return

        device_type = state.device_type.current_value
        device_uid = state.device_uid.text.strip()
        strip_id = state.strip_id.text.strip()
        length_text = state.length.text.strip()

        error = validate_device_uid(device_uid)
        if error is not None:
            self._set_dialog_error(error, state.device_uid)
            return

        error = validate_strip_id(strip_id)
        if error is not None:
            self._set_dialog_error(error, state.strip_id)
            return

        length, error = parse_length(length_text)
        if error is not None:
            self._set_dialog_error(error, state.length)
            return

        client = self._get_client()
        if client is None:
            self._set_dialog_error('controller not connected', state.device_uid)
            return

        cmd_id = self._next_id
        state.pending_request_id = cmd_id
        state.error_text = ''
        cmd = {
            'cmd': 'add_device',
            'device_uid': device_uid,
            'device_type': device_type,
            'strip_id': strip_id,
            'length': length,
            'id': cmd_id,
        }
        self._next_id += 1
        try:
            client.send_cmd(cmd)
        except OSError as e:
            state.pending_request_id = None
            self._set_dialog_error(f'send failed: {e}', state.device_uid)
            return

    def _program_manager_options(self) -> list[tuple[str, str]]:
        options = []
        for entry in self._loadable_program_catalog():
            beat_text = '?' if entry.beat is None else f'{entry.beat:g}'
            duration_text = '?' if entry.duration is None else f'{entry.duration:g}s'
            label = (
                f'{entry.program_id:<18s} '
                f'{duration_text:<7s} '
                f'beat {beat_text:<6s} '
                '[ok]'
            )
            options.append((entry.program_id, label))
        return options

    def _loadable_program_catalog(self) -> list[ProgramCatalogEntry]:
        return [entry for entry in self._program_catalog if entry.error is None]

    def _hidden_program_count(self) -> int:
        return sum(1 for entry in self._program_catalog if entry.error is not None)

    def _selected_program_from_manager(self) -> ProgramCatalogEntry | None:
        if not isinstance(self._active_modal, ProgramManagerDialogState):
            return None
        if self._active_modal.program_list is None:
            return None
        selected_program_id = self._active_modal.program_list.current_value
        loadable = self._loadable_program_catalog()
        for entry in loadable:
            if entry.program_id == selected_program_id:
                return entry
        return loadable[0] if loadable else None

    def _eligible_target_devices_for_strip(self, strip_id: str) -> list[DeviceCatalogEntry]:
        return [
            entry
            for entry in self._device_catalog
            if entry.strip_id == strip_id
        ]

    def _eligible_target_devices_by_strip(
        self,
        strip_ids: list[str],
    ) -> list[tuple[str, list[DeviceCatalogEntry]]]:
        return [
            (strip_id, self._eligible_target_devices_for_strip(strip_id))
            for strip_id in strip_ids
        ]

    def _refresh_program_manager_dialog(self) -> None:
        if not isinstance(self._active_modal, ProgramManagerDialogState):
            return
        modal = self._active_modal
        options = self._program_manager_options()
        if modal.program_list is None:
            if options:
                self._open_program_manager(
                    error_text=modal.error_text,
                    pending_request_id=modal.pending_request_id,
                    loop_enabled=modal.loop_enabled,
                )
            else:
                self._app.invalidate()
            return
        if not options:
            self._open_program_manager(
                error_text=modal.error_text,
                pending_request_id=modal.pending_request_id,
                loop_enabled=modal.loop_enabled,
            )
            return
        selected_program_id = modal.program_list.current_value
        modal.program_list.values = options
        if not any(program_id == selected_program_id for program_id, _ in options):
            selected_program_id = options[0][0]
        modal.program_list.current_value = selected_program_id
        self._app.invalidate()

    def _refresh_program_target_dialog(self) -> None:
        modal = self._active_modal
        if not isinstance(modal, ProgramTargetDialogState):
            return
        self._open_program_target_dialog(
            program_id=modal.program_id,
            strip_ids=modal.strip_ids,
            selected_targets_by_strip={
                section.strip_id: list(section.target_list.current_values)
                for section in modal.strip_sections
                if section.target_list is not None
            },
            error_text=modal.error_text,
            pending_request_id=modal.pending_request_id,
            loop_enabled=modal.loop_enabled,
        )

    def _program_manager_detail_text(self) -> str:
        state = self._active_modal
        if not isinstance(state, ProgramManagerDialogState):
            return ' '
        entry = self._selected_program_from_manager()
        if entry is None:
            hidden = self._hidden_program_count()
            if hidden:
                noun = 'program' if hidden == 1 else 'programs'
                return f'No loadable programs. {hidden} broken {noun} hidden.'
            return 'No programs in library.'
        duration_text = '?' if entry.duration is None else f'{entry.duration:g}s'
        beat_text = '?' if entry.beat is None else f'{entry.beat:g}'
        loop_text = 'On' if state.loop_enabled else 'Off'
        detail = (
            f'Selected: {entry.program_id}   duration: {duration_text}   '
            f'beat: {beat_text}   loop: {loop_text}'
        )
        if entry.strips:
            if len(entry.strips) == 1:
                eligible = len(self._eligible_target_devices_for_strip(entry.strips[0]))
                detail += f'   strip: {entry.strips[0]}   eligible targets: {eligible}'
            else:
                detail += f'   strips: {", ".join(entry.strips)}   target selection available'
        hidden = self._hidden_program_count()
        if hidden:
            noun = 'program' if hidden == 1 else 'programs'
            detail += f'   {hidden} broken {noun} hidden'
        return detail

    def _program_target_detail_text(self) -> str:
        state = self._active_modal
        if not isinstance(state, ProgramTargetDialogState):
            return ' '
        counts = []
        total = 0
        for section in state.strip_sections:
            selected = 0 if section.target_list is None else len(section.target_list.current_values)
            counts.append(f'{section.strip_id}={selected}')
            total += selected
        loop_text = 'On' if state.loop_enabled else 'Off'
        return (
            f'Program: {state.program_id}   selected: {", ".join(counts)}   '
            f'total targets: {total}   loop: {loop_text}'
        )

    def _toggle_program_loop(self) -> None:
        state = self._active_modal
        if not isinstance(state, ProgramManagerDialogState):
            return
        if state.pending_request_id is not None:
            return
        state.loop_enabled = not state.loop_enabled
        if state.loop_toggle_button is not None:
            state.loop_toggle_button.text = (
                'Loop: On' if state.loop_enabled else 'Loop: Off'
            )
        self._app.invalidate()

    def _toggle_program_target_loop(self) -> None:
        state = self._active_modal
        if not isinstance(state, ProgramTargetDialogState):
            return
        if state.pending_request_id is not None:
            return
        state.loop_enabled = not state.loop_enabled
        state.loop_toggle_button.text = (
            'Loop: On' if state.loop_enabled else 'Loop: Off'
        )
        self._app.invalidate()

    def _open_program_manager(
        self,
        *,
        selected_program_id: str | None = None,
        error_text: str = '',
        pending_request_id: int | None = None,
        loop_enabled: bool = False,
    ) -> None:
        options = self._program_manager_options()
        error_control = FormattedTextControl(
            text=lambda: (
                self._active_modal.error_text
                if isinstance(self._active_modal, ProgramManagerDialogState)
                else ' '
            )
        )
        detail_control = FormattedTextControl(text=self._program_manager_detail_text)
        if options:
            if selected_program_id is None or not any(
                program_id == selected_program_id for program_id, _ in options
            ):
                selected_program_id = options[0][0]
            program_list = RadioList(
                values=options,
                default=selected_program_id,
                select_on_focus=True,
            )
            load_button = DialogButton('Load', handler=self._submit_program_load)
            loop_toggle_button = DialogButton(
                'Loop: On' if loop_enabled else 'Loop: Off',
                handler=self._toggle_program_loop,
            )
            publish_button = DialogButton('Publish', handler=self._start_program_publish)
            rescan_button = DialogButton('Rescan', handler=self._submit_program_rescan)
            close_button = DialogButton('Close', handler=self._close_modal)
            dialog = Dialog(
                title='Programs',
                body=HSplit([
                    Label(text='Controller program library', style='class:newdevice.label'),
                    program_list,
                    Window(height=1, content=detail_control),
                    Window(height=1, content=error_control, style='class:newdevice.error'),
                    Label(
                        text='Enter: load   Tab: move   Esc: close',
                        style='class:newdevice.help',
                    ),
                ]),
                buttons=[load_button, loop_toggle_button, publish_button, rescan_button, close_button],
                with_background=True,
            )
            state = ProgramManagerDialogState(
                program_list=program_list,
                load_button=load_button,
                loop_toggle_button=loop_toggle_button,
                publish_button=publish_button,
                rescan_button=rescan_button,
                close_button=close_button,
                dialog=dialog,
                error_text=error_text,
                pending_request_id=pending_request_id,
                loop_enabled=loop_enabled,
            )
            self._set_modal(state, focus=program_list)
            return

        publish_button = DialogButton('Publish', handler=self._start_program_publish)
        rescan_button = DialogButton('Rescan', handler=self._submit_program_rescan)
        close_button = DialogButton('Close', handler=self._close_modal)
        hidden_control = FormattedTextControl(text=self._program_manager_detail_text)
        dialog = Dialog(
            title='Programs',
            body=HSplit([
                Label(text='No loadable programs.', style='class:newdevice.label'),
                Window(height=1, content=hidden_control),
                Window(height=1, content=error_control, style='class:newdevice.error'),
                Label(
                    text='Publish a local file or rescan the controller library.',
                    style='class:newdevice.help',
                ),
            ]),
            buttons=[publish_button, rescan_button, close_button],
            with_background=True,
        )
        state = ProgramManagerDialogState(
            program_list=None,
            load_button=None,
            loop_toggle_button=None,
            publish_button=publish_button,
            rescan_button=rescan_button,
            close_button=close_button,
            dialog=dialog,
            error_text=error_text,
            pending_request_id=pending_request_id,
            loop_enabled=False,
        )
        self._set_modal(state, focus=publish_button)

    def _open_program_target_dialog(
        self,
        *,
        program_id: str,
        strip_ids: list[str],
        selected_targets_by_strip: dict[str, list[str]] | None = None,
        error_text: str = '',
        pending_request_id: int | None = None,
        loop_enabled: bool = False,
    ) -> None:
        selected_targets_by_strip = selected_targets_by_strip or {}
        error_control = FormattedTextControl(
            text=lambda: (
                self._active_modal.error_text
                if isinstance(self._active_modal, ProgramTargetDialogState)
                else ' '
            )
        )
        detail_control = FormattedTextControl(text=self._program_target_detail_text)
        strip_sections: list[ProgramTargetSection] = []
        body_children: list = [
            Label(text=f'Program: {program_id}', style='class:newdevice.label'),
        ]
        has_any_options = False
        for strip_id, eligible in self._eligible_target_devices_by_strip(strip_ids):
            options = [
                (
                    entry.device_uid,
                    f'{entry.device_uid:<18s} '
                    f'{str(entry.length if entry.length is not None else "?"):<5s} '
                    f'{"online" if entry.connected else "offline"}',
                )
                for entry in eligible
            ]
            selected_targets = selected_targets_by_strip.get(strip_id)
            if selected_targets is None:
                selected_targets = [device_uid for device_uid, _label in options]
            else:
                selected_targets = [
                    device_uid
                    for device_uid in selected_targets
                    if any(device_uid == option_uid for option_uid, _label in options)
                ]
            if options:
                has_any_options = True
                target_list = CheckboxList(values=options, default_values=selected_targets)
            else:
                target_list = None
            strip_sections.append(ProgramTargetSection(strip_id=strip_id, target_list=target_list))
            body_children.append(Label(text=f'Strip: {strip_id}', style='class:newdevice.label'))
            if target_list is not None:
                body_children.append(target_list)
            else:
                body_children.append(Label(
                    text=f'No configured devices match strip "{strip_id}".',
                    style='class:newdevice.help',
                ))

        load_button = (
            DialogButton('Load', handler=self._submit_program_target_load)
            if has_any_options
            else None
        )

        loop_toggle_button = DialogButton(
            'Loop: On' if loop_enabled else 'Loop: Off',
            handler=self._toggle_program_target_loop,
        )
        back_button = DialogButton('Back', handler=self._cancel_program_target_dialog)
        close_button = DialogButton('Close', handler=self._close_modal)
        body_children.extend([
            Window(height=1, content=detail_control),
            Window(height=1, content=error_control, style='class:newdevice.error'),
            Label(
                text='Space: toggle target   Tab: move   Esc: close',
                style='class:newdevice.help',
            ),
        ])
        dialog = Dialog(
            title='Load targets',
            body=HSplit(body_children),
            buttons=[button for button in [load_button, loop_toggle_button, back_button, close_button] if button is not None],
            with_background=True,
        )
        state = ProgramTargetDialogState(
            program_id=program_id,
            strip_sections=strip_sections,
            load_button=load_button,
            loop_toggle_button=loop_toggle_button,
            back_button=back_button,
            close_button=close_button,
            dialog=dialog,
            error_text=error_text,
            pending_request_id=pending_request_id,
            loop_enabled=loop_enabled,
        )
        focus_target = next(
            (section.target_list for section in strip_sections if section.target_list is not None),
            back_button,
        )
        self._set_modal(state, focus=focus_target)

    def _start_program_publish(self) -> None:
        if not isinstance(self._active_modal, ProgramManagerDialogState):
            return
        if self._active_modal.pending_request_id is not None:
            return

        selected_program_id = None
        selected_entry = self._selected_program_from_manager()
        if selected_entry is not None:
            selected_program_id = selected_entry.program_id

        path = TextArea(multiline=False, wrap_lines=False)
        program_id = TextArea(
            text='' if selected_program_id is None else selected_program_id,
            multiline=False,
            wrap_lines=False,
        )
        submit_button = DialogButton('Publish', handler=self._submit_program_publish_dialog)
        cancel_button = DialogButton('Cancel', handler=self._cancel_program_publish_dialog)
        path.buffer.accept_handler = lambda buff: self._focus_dialog_widget(program_id)
        program_id.buffer.accept_handler = lambda buff: self._focus_dialog_widget(submit_button)
        error_control = FormattedTextControl(
            text=lambda: (
                self._active_modal.error_text
                if isinstance(self._active_modal, ProgramPublishDialogState)
                else ' '
            )
        )
        dialog = Dialog(
            title='Publish program',
            body=HSplit([
                Label(text='Local file path', style='class:newdevice.label'),
                path,
                Label(text='Program id (optional)', style='class:newdevice.label'),
                program_id,
                Window(height=1, content=error_control, style='class:newdevice.error'),
                Label(
                    text='Enter: next   Tab: move   Esc: cancel',
                    style='class:newdevice.help',
                ),
            ]),
            buttons=[submit_button, cancel_button],
            with_background=True,
        )
        state = ProgramPublishDialogState(
            path=path,
            program_id=program_id,
            submit_button=submit_button,
            cancel_button=cancel_button,
            dialog=dialog,
            return_selected_program_id=selected_program_id,
        )
        self._set_modal(state, focus=path)

    def _cancel_program_publish_dialog(self) -> None:
        state = self._active_modal
        if not isinstance(state, ProgramPublishDialogState):
            return
        self._open_program_manager(selected_program_id=state.return_selected_program_id)

    def _cancel_program_target_dialog(self) -> None:
        state = self._active_modal
        if not isinstance(state, ProgramTargetDialogState):
            return
        self._open_program_manager(
            selected_program_id=state.program_id,
            loop_enabled=state.loop_enabled,
        )

    def _submit_program_publish_dialog(self) -> None:
        state = self._active_modal
        if not isinstance(state, ProgramPublishDialogState):
            return
        if state.pending_request_id is not None:
            return

        path = state.path.text.strip()
        program_id = self._program_id_from_publish_fields(path, state.program_id.text.strip())
        if not path:
            state.error_text = 'publish path is required'
            self._app.layout.focus(state.path)
            self._app.invalidate()
            return
        if not program_id:
            state.error_text = f"could not derive program id from {path!r}"
            self._app.layout.focus(state.program_id)
            self._app.invalidate()
            return

        source, error = self._read_publish_source(path)
        if error is not None:
            state.error_text = error
            self._app.layout.focus(state.path)
            self._app.invalidate()
            return

        cmd_id, error = self._send_program_publish_request(program_id, source)
        if error is not None:
            state.error_text = error
            self._app.invalidate()
            return
        state.pending_request_id = cmd_id
        state.error_text = ''

    def _submit_program_load(self) -> None:
        state = self._active_modal
        if not isinstance(state, ProgramManagerDialogState):
            return
        if state.pending_request_id is not None:
            return

        entry = self._selected_program_from_manager()
        if entry is None:
            state.error_text = 'no programs in library'
            self._app.invalidate()
            return

        if entry.strips:
            self._open_program_target_dialog(
                program_id=entry.program_id,
                strip_ids=entry.strips,
                loop_enabled=state.loop_enabled,
            )
            return

        cmd_id, error = self._send_program_load_request(
            entry.program_id,
            loop=state.loop_enabled,
        )
        if error is not None:
            state.error_text = error
            self._app.invalidate()
            return
        state.pending_request_id = cmd_id
        state.error_text = ''

    def _submit_program_target_load(self) -> None:
        state = self._active_modal
        if not isinstance(state, ProgramTargetDialogState):
            return
        if state.pending_request_id is not None:
            return
        selected_targets: list[str] = []
        for section in state.strip_sections:
            if section.target_list is None:
                state.error_text = f'no configured devices match strip "{section.strip_id}"'
                self._app.invalidate()
                return
            section_targets = list(section.target_list.current_values)
            if not section_targets:
                if len(state.strip_sections) == 1:
                    state.error_text = 'select at least one target device'
                else:
                    state.error_text = f'select at least one target for strip "{section.strip_id}"'
                self._app.invalidate()
                return
            selected_targets.extend(section_targets)
        if len(selected_targets) != len(set(selected_targets)):
            state.error_text = 'duplicate target selected'
            self._app.invalidate()
            return
        cmd_id, error = self._send_program_load_request(
            state.program_id,
            loop=state.loop_enabled,
            targets=selected_targets,
        )
        if error is not None:
            state.error_text = error
            self._app.invalidate()
            return
        state.pending_request_id = cmd_id
        state.error_text = ''

    def _submit_program_rescan(self) -> None:
        state = self._active_modal
        if not isinstance(state, ProgramManagerDialogState):
            return
        if state.pending_request_id is not None:
            return

        cmd_id, error = self._send_program_rescan_request()
        if error is not None:
            state.error_text = error
            self._app.invalidate()
            return
        state.pending_request_id = cmd_id
        state.error_text = ''

    def _session_manager_mode(self) -> str:
        if self._get_client() is None and not self._controller_connected:
            return 'disconnected'
        if self._session is None:
            return 'empty'
        return 'active'

    def _refresh_session_dialog(self) -> None:
        if not isinstance(self._active_modal, SessionManagerDialogState):
            return
        modal = self._active_modal
        mode = self._session_manager_mode()
        if modal.mode != mode:
            self._open_session_manager(
                error_text=modal.error_text,
                pending_request_id=modal.pending_request_id,
            )
            return
        self._app.invalidate()

    def _session_summary_text(self) -> str:
        session = self._session
        if session is None:
            if self._get_client() is None and not self._controller_connected:
                return 'Controller not connected.'
            return 'No program loaded.\nUse /programs to publish or load one.'

        lines: list[str] = []
        sid = '?' if session.session_id is None else str(session.session_id)
        state = session.playback_state or '?'
        lines.append(f'State: {state}')
        lines.append(f'Session: {sid}')
        if session.epoch is not None:
            lines.append(f'Epoch: {session.epoch}')
        if session.current_t_rel is not None and session.duration is not None:
            lines.append(f'Time: {session.current_t_rel:.2f}/{session.duration:.2f}s')
        elif session.duration is not None:
            lines.append(f'Duration: {session.duration:.2f}s')
        strips = session.strips or []
        if strips:
            strip_summary = ', '.join(
                (
                    f'{strip.name} ({strip.length if strip.length is not None else "?"})'
                    if not strip.targets else
                    f'{strip.name} ({strip.length if strip.length is not None else "?"}) '
                    f'[{", ".join(target.device_uid for target in strip.targets)}]'
                )
                for strip in strips
            )
            lines.append(f'Strips: {strip_summary}')
        safe_intervals = session.safe_intervals or []
        lines.append(f'Safe intervals: {len(safe_intervals)}')
        return '\n'.join(lines)

    def _open_session_manager(
        self,
        *,
        error_text: str = '',
        pending_request_id: int | None = None,
    ) -> None:
        mode = self._session_manager_mode()
        error_control = FormattedTextControl(
            text=lambda: (
                self._active_modal.error_text
                if isinstance(self._active_modal, SessionManagerDialogState)
                else ' '
            )
        )
        summary_control = FormattedTextControl(text=lambda: self._session_summary_text())

        if mode == 'active':
            play_button = DialogButton('Play', handler=lambda: self._submit_session_command('play'))
            pause_button = DialogButton('Pause', handler=lambda: self._submit_session_command('pause'))
            stop_button = DialogButton('Stop', handler=lambda: self._submit_session_command('stop'))
            seek_button = DialogButton('Seek', handler=self._start_session_seek)
            close_button = DialogButton('Close', handler=self._close_modal)
            dialog = Dialog(
                title='Session',
                body=HSplit([
                    Window(content=summary_control),
                    Window(height=1, content=error_control, style='class:newdevice.error'),
                    Label(
                        text='Use buttons to control the live session.',
                        style='class:newdevice.help',
                    ),
                ]),
                buttons=[play_button, pause_button, stop_button, seek_button, close_button],
                with_background=True,
            )
            state = SessionManagerDialogState(
                mode=mode,
                play_button=play_button,
                pause_button=pause_button,
                stop_button=stop_button,
                seek_button=seek_button,
                programs_button=None,
                close_button=close_button,
                dialog=dialog,
                error_text=error_text,
                pending_request_id=pending_request_id,
            )
            self._set_modal(state, focus=play_button)
            return

        close_button = DialogButton('Close', handler=self._close_modal)
        buttons: list[Button] = []
        programs_button: Button | None = None
        if mode == 'empty':
            programs_button = DialogButton('Programs', handler=self._open_programs_from_session)
            buttons.append(programs_button)
        buttons.append(close_button)
        dialog = Dialog(
            title='Session',
            body=HSplit([
                Window(content=summary_control),
                Window(height=1, content=error_control, style='class:newdevice.error'),
            ]),
            buttons=buttons,
            with_background=True,
        )
        state = SessionManagerDialogState(
            mode=mode,
            play_button=None,
            pause_button=None,
            stop_button=None,
            seek_button=None,
            programs_button=programs_button,
            close_button=close_button,
            dialog=dialog,
            error_text=error_text,
            pending_request_id=pending_request_id,
        )
        self._set_modal(state, focus=programs_button or close_button)

    def _open_programs_from_session(self) -> None:
        if not isinstance(self._active_modal, SessionManagerDialogState):
            return
        if not self._program_catalog_ready:
            self._active_modal.error_text = 'program list not available yet'
            self._app.invalidate()
            return
        self._open_program_manager()

    def _submit_session_command(self, command: str) -> None:
        state = self._active_modal
        if not isinstance(state, SessionManagerDialogState):
            return
        if state.pending_request_id is not None:
            return
        if self._session is None:
            state.error_text = 'no program loaded'
            self._app.invalidate()
            return
        cmd_id, error = self._send_controller_cmd({'cmd': command})
        if error is not None:
            state.error_text = error
            self._app.invalidate()
            return
        state.pending_request_id = cmd_id
        state.error_text = ''

    def _start_session_seek(self) -> None:
        state = self._active_modal
        if not isinstance(state, SessionManagerDialogState):
            return
        if state.pending_request_id is not None:
            return
        if self._session is None:
            state.error_text = 'no program loaded'
            self._app.invalidate()
            return
        current_t_rel = self._session.current_t_rel
        t_rel = TextArea(
            text='' if current_t_rel is None else f'{current_t_rel:g}',
            multiline=False,
            wrap_lines=False,
        )
        submit_button = DialogButton('OK', handler=self._submit_session_seek_dialog)
        cancel_button = DialogButton('Cancel', handler=self._cancel_session_seek_dialog)
        t_rel.buffer.accept_handler = lambda buff: self._focus_dialog_widget(submit_button)
        error_control = FormattedTextControl(
            text=lambda: (
                self._active_modal.error_text
                if isinstance(self._active_modal, SessionSeekDialogState)
                else ' '
            )
        )
        dialog = Dialog(
            title='Seek session',
            body=HSplit([
                Label(text='Target time (seconds)', style='class:newdevice.label'),
                t_rel,
                Window(height=1, content=error_control, style='class:newdevice.error'),
                Label(
                    text='Enter: seek   Tab: move   Esc: cancel',
                    style='class:newdevice.help',
                ),
            ]),
            buttons=[submit_button, cancel_button],
            with_background=True,
        )
        self._set_modal(
            SessionSeekDialogState(
                t_rel=t_rel,
                submit_button=submit_button,
                cancel_button=cancel_button,
                dialog=dialog,
            ),
            focus=t_rel,
        )

    def _cancel_session_seek_dialog(self) -> None:
        if not isinstance(self._active_modal, SessionSeekDialogState):
            return
        self._open_session_manager()

    def _submit_session_seek_dialog(self) -> None:
        state = self._active_modal
        if not isinstance(state, SessionSeekDialogState):
            return
        if state.pending_request_id is not None:
            return

        t_rel, error = parse_t_rel(state.t_rel.text.strip())
        if error is not None:
            state.error_text = error
            self._app.layout.focus(state.t_rel)
            self._app.invalidate()
            return

        cmd_id, error = self._send_seek_request(t_rel)
        if error is not None:
            state.error_text = error
            self._app.invalidate()
            return
        state.pending_request_id = cmd_id
        state.error_text = ''

    def _device_manager_options(self) -> list[tuple[str, str]]:
        options = []
        for entry in self._device_catalog:
            status = 'online' if entry.connected else 'offline'
            device_type = entry.device_type or '?'
            length_text = '?' if entry.length is None else str(entry.length)
            label = (
                f'{entry.device_uid:<16s} '
                f'{device_type:<5s} '
                f'{entry.strip_id:<12s} '
                f'{length_text:>4s} LEDs '
                f'[{status}]'
            )
            options.append((entry.device_uid, label))
        return options

    def _selected_device_from_manager(self) -> DeviceCatalogEntry | None:
        if not isinstance(self._active_modal, DeviceManagerDialogState):
            return None
        if self._active_modal.device_list is None:
            return None
        selected_uid = self._active_modal.device_list.current_value
        for entry in self._device_catalog:
            if entry.device_uid == selected_uid:
                return entry
        return self._device_catalog[0] if self._device_catalog else None

    def _refresh_device_manager_dialog(self) -> None:
        if not isinstance(self._active_modal, DeviceManagerDialogState):
            return
        modal = self._active_modal
        options = self._device_manager_options()
        if modal.device_list is None:
            if options:
                self._open_device_manager()
            else:
                self._app.invalidate()
            return
        if not options:
            self._open_device_manager()
            return
        selected_uid = modal.device_list.current_value
        modal.device_list.values = options
        if not any(uid == selected_uid for uid, _ in options):
            selected_uid = options[0][0]
        modal.device_list.current_value = selected_uid
        self._app.invalidate()

    def _open_device_manager(self) -> None:
        options = self._device_manager_options()
        if not options:
            add_button = DialogButton('Add', handler=self._start_newdevice_from_devices)
            close_button = DialogButton('Close', handler=self._close_modal)
            dialog = Dialog(
                title='Devices',
                body=HSplit([
                    Label(text='No configured devices.', style='class:newdevice.label'),
                    Label(
                        text='Add a device to begin.',
                        style='class:newdevice.help',
                    ),
                ]),
                buttons=[add_button, close_button],
                with_background=True,
            )
            state = DeviceManagerDialogState(
                device_list=None,
                edit_button=None,
                remove_button=None,
                add_button=add_button,
                close_button=close_button,
                dialog=dialog,
            )
            self._set_modal(state, focus=add_button)
            return
        device_list = RadioList(values=options, default=options[0][0], select_on_focus=True)
        edit_button = DialogButton('Edit', handler=self._start_device_edit)
        remove_button = DialogButton('Remove', handler=self._start_device_remove)
        close_button = DialogButton('Close', handler=self._close_modal)
        dialog = Dialog(
            title='Devices',
            body=HSplit([
                Label(text='Configured devices', style='class:newdevice.label'),
                device_list,
                Label(
                    text='Enter: edit   Tab: move   Esc: close',
                    style='class:newdevice.help',
                ),
            ]),
            buttons=[edit_button, remove_button, close_button],
            with_background=True,
        )
        state = DeviceManagerDialogState(
            device_list=device_list,
            edit_button=edit_button,
            remove_button=remove_button,
            add_button=None,
            close_button=close_button,
            dialog=dialog,
        )
        self._set_modal(state, focus=device_list)

    def _start_newdevice_from_devices(self) -> None:
        if not isinstance(self._active_modal, DeviceManagerDialogState):
            return
        if self._get_client() is None:
            self._local_log("controller not connected")
            return
        self._open_newdevice_dialog()

    def _start_device_edit(self) -> None:
        entry = self._selected_device_from_manager()
        if entry is None:
            return
        device_uid = TextArea(text=entry.device_uid, multiline=False, wrap_lines=False)
        strip_id = TextArea(text=entry.strip_id, multiline=False, wrap_lines=False)
        length = TextArea(
            text='' if entry.length is None else str(entry.length),
            multiline=False,
            wrap_lines=False,
        )
        submit_button = DialogButton('OK', handler=self._submit_device_edit_dialog)
        cancel_button = DialogButton('Cancel', handler=self._close_modal)
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
            text=lambda: (
                self._active_modal.error_text
                if isinstance(self._active_modal, DeviceEditDialogState)
                else ' '
            )
        )
        device_type = entry.device_type or '?'
        dialog = Dialog(
            title=f'Edit device {entry.device_uid}',
            body=HSplit([
                Label(text=f'Device type: {device_type}', style='class:newdevice.label'),
                Label(text='Device uid', style='class:newdevice.label'),
                device_uid,
                Label(text='Strip id', style='class:newdevice.label'),
                strip_id,
                Label(text='Length', style='class:newdevice.label'),
                length,
                Window(height=1, content=error_control, style='class:newdevice.error'),
                Label(
                    text='Enter: next   Tab: move   Esc: cancel',
                    style='class:newdevice.help',
                ),
            ]),
            buttons=[submit_button, cancel_button],
            with_background=True,
        )
        state = DeviceEditDialogState(
            target_device_uid=entry.device_uid,
            device_type=entry.device_type,
            device_uid=device_uid,
            strip_id=strip_id,
            length=length,
            submit_button=submit_button,
            cancel_button=cancel_button,
            dialog=dialog,
        )
        self._set_modal(state, focus=device_uid)

    def _submit_device_edit_dialog(self) -> None:
        state = self._active_modal
        if not isinstance(state, DeviceEditDialogState):
            return
        if state.pending_request_id is not None:
            return

        device_uid = state.device_uid.text.strip()
        strip_id = state.strip_id.text.strip()
        length_text = state.length.text.strip()

        error = validate_device_uid(device_uid)
        if error is not None:
            state.error_text = error
            self._app.layout.focus(state.device_uid)
            self._app.invalidate()
            return
        error = validate_strip_id(strip_id)
        if error is not None:
            state.error_text = error
            self._app.layout.focus(state.strip_id)
            self._app.invalidate()
            return
        length, error = parse_length(length_text)
        if error is not None:
            state.error_text = error
            self._app.layout.focus(state.length)
            self._app.invalidate()
            return

        client = self._get_client()
        if client is None:
            state.error_text = 'controller not connected'
            self._app.invalidate()
            return

        cmd_id = self._next_id
        state.pending_request_id = cmd_id
        state.error_text = ''
        self._next_id += 1
        try:
            client.send_cmd({
                'cmd': 'edit_device',
                'target_device_uid': state.target_device_uid,
                'device_uid': device_uid,
                'strip_id': strip_id,
                'length': length,
                'id': cmd_id,
            })
        except OSError as e:
            state.pending_request_id = None
            state.error_text = f'send failed: {e}'
            self._app.invalidate()

    def _start_device_remove(self) -> None:
        entry = self._selected_device_from_manager()
        if entry is None:
            return
        confirm_button = DialogButton('OK', handler=self._submit_device_remove_dialog)
        cancel_button = DialogButton('Cancel', handler=self._close_modal)
        error_control = FormattedTextControl(
            text=lambda: (
                self._active_modal.error_text
                if isinstance(self._active_modal, DeviceRemoveDialogState)
                else ' '
            )
        )
        dialog = Dialog(
            title='Remove device',
            body=HSplit([
                Label(text=f'Remove device {entry.device_uid}?'),
                Window(height=1, content=error_control, style='class:newdevice.error'),
            ]),
            buttons=[confirm_button, cancel_button],
            with_background=True,
        )
        state = DeviceRemoveDialogState(
            device_uid=entry.device_uid,
            confirm_button=confirm_button,
            cancel_button=cancel_button,
            dialog=dialog,
        )
        self._set_modal(state, focus=confirm_button)

    def _submit_device_remove_dialog(self) -> None:
        state = self._active_modal
        if not isinstance(state, DeviceRemoveDialogState):
            return
        if state.pending_request_id is not None:
            return

        client = self._get_client()
        if client is None:
            state.error_text = 'controller not connected'
            self._app.invalidate()
            return

        cmd_id = self._next_id
        state.pending_request_id = cmd_id
        state.error_text = ''
        self._next_id += 1
        try:
            client.send_cmd({
                'cmd': 'remove_device',
                'device_uid': state.device_uid,
                'id': cmd_id,
            })
        except OSError as e:
            state.pending_request_id = None
            state.error_text = f'send failed: {e}'
            self._app.invalidate()

    def _send_controller_cmd(self, payload: dict) -> tuple[int | None, str | None]:
        client = self._get_client()
        if client is None:
            return None, 'not connected'
        cmd_id = self._next_id
        self._next_id += 1
        try:
            client.send_cmd({**payload, 'id': cmd_id})
        except OSError as e:
            return None, f'send failed: {e}'
        return cmd_id, None

    def _send_seek_request(self, t_rel: float) -> tuple[int | None, str | None]:
        return self._send_controller_cmd({'cmd': 'seek', 't_rel': t_rel})

    @staticmethod
    def _program_id_from_publish_fields(path: str, program_id: str) -> str | None:
        if program_id:
            return program_id
        if not path:
            return None
        derived = Path(path).stem
        return derived or None

    @staticmethod
    def _read_publish_source(path: str) -> tuple[str | None, str | None]:
        expanded_path = os.path.expanduser(path)
        try:
            return Path(expanded_path).read_text(), None
        except OSError as e:
            return None, f'cannot read {path}: {e}'

    def _send_program_rescan_request(self) -> tuple[int | None, str | None]:
        return self._send_controller_cmd({'cmd': 'rescan_programs'})

    def _send_program_load_request(
        self,
        program_id: str,
        *,
        loop: bool,
        targets: list[str] | None = None,
    ) -> tuple[int | None, str | None]:
        cmd = {
            'cmd': 'load_program',
            'program_id': program_id,
            'loop': loop,
        }
        if targets is not None:
            cmd['targets'] = list(targets)
        return self._send_controller_cmd(cmd)

    def _send_program_publish_request(
        self,
        program_id: str,
        source: str | None,
    ) -> tuple[int | None, str | None]:
        if source is None:
            return None, 'publish source is required'
        return self._send_controller_cmd({
            'cmd': 'publish_program',
            'program_id': program_id,
            'source': source,
        })

    def _do_rescan(self) -> None:
        _cmd_id, error = self._send_program_rescan_request()
        if error is not None:
            self._local_log(error)

    def _resolve_program_entry(self, cmd: dict) -> ProgramCatalogEntry | None:
        index = cmd.get('index')
        target = cmd.get('target')

        if index is not None:
            if index < 1 or index > len(self._program_catalog):
                self._local_log(
                    f"index #{index} out of range "
                    f"(have {len(self._program_catalog)} programs)"
                )
                return None
            return self._program_catalog[index - 1]

        for entry in self._program_catalog:
            if entry.program_id == target:
                return entry
        self._local_log(f"program not found: {target}")
        return None

    def _do_publish(self, cmd: dict) -> None:
        path = cmd.get('path')
        program_id = cmd.get('program_id')
        if not isinstance(path, str) or not path:
            self._local_log("publish path is required")
            return
        if not isinstance(program_id, str) or not program_id:
            self._local_log("publish program id is required")
            return

        source, error = self._read_publish_source(path)
        if error is not None:
            self._local_log(error)
            return
        if program_id == Path(path).stem:
            self._local_log(f"> /publish {path}")
        else:
            self._local_log(f"> /publish {path} as {program_id}")
        _cmd_id, error = self._send_program_publish_request(program_id, source)
        if error is not None:
            self._local_log(error)

    def _do_load(self, cmd: dict) -> None:
        if self._get_client() is None:
            self._local_log("not connected")
            return
        if not self._program_catalog_ready:
            self._local_log("program list not available yet")
            return
        if not self._program_catalog:
            self._local_log("no known programs, try /rescan")
            return

        loop = cmd.get('loop', False)
        entry = self._resolve_program_entry(cmd)
        if entry is None:
            return
        if entry.error:
            self._local_log(f"cannot load {entry.program_id}: {entry.error}")
            return

        loop_str = ' loop' if loop else ''
        self._local_log(f"> /load {entry.program_id}{loop_str}")
        _cmd_id, error = self._send_program_load_request(entry.program_id, loop=loop)
        if error is not None:
            self._local_log(error)

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

            has_buffered = getattr(client, 'has_buffered_messages', lambda: False)
            if not has_buffered():
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
        '--log-dir', default=None,
        help='logs directory (default: <repo>/logs)',
    )
    args = parser.parse_args()

    log_dir = os.path.expanduser(args.log_dir or DEFAULT_LOGS_PATH)
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    TuiApp(
        args.socket,
        log_file=str(Path(log_dir) / 'tui.log'),
    ).run()


if __name__ == '__main__':
    main()
