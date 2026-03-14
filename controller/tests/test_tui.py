"""Tests for TUI non-UI logic."""

import datetime
import json
import socket
from pathlib import Path

import pytest

from elemctl.tui import (
    CommandReplyUpdate,
    ControllerConnectionUpdate,
    DeviceCatalogEntry,
    DeviceCatalogSnapshotUpdate,
    DeviceEditDialogState,
    DeviceManagerDialogState,
    DevicePanelEntry,
    DeviceRemoveDialogState,
    PanelDeviceInfo,
    PanelDeviceStatusUpdate,
    PanelSnapshotUpdate,
    SessionInfo,
    SessionManagerDialogState,
    SessionSeekDialogState,
    SessionStateUpdate,
    SessionStripEntry,
    ProgramManagerDialogState,
    ProgramPublishDialogState,
    ProgramCatalogEntry,
    ProgramCatalogUpdate,
    TuiApp,
    _decode_tui_message,
    format_event,
    parse_command,
    format_transcript_line,
    parse_length,
    parse_t_rel,
    validate_device_uid,
    validate_strip_id,
    _DEVICES_SENTINEL,
    _HELP_SENTINEL,
    _NEWDEVICE_SENTINEL,
    _PROGRAMS_SENTINEL,
    _QUIT_SENTINEL,
    _RESCAN_SENTINEL,
    _SESSION_SENTINEL,
)
from elemctl.uds_wire import KIND_JSON, KIND_FRAME, UdsReader, encode_json
from elemctl.uds_client import UdsClient


# ---------------------------------------------------------------------------
# format_event tests
# ---------------------------------------------------------------------------

def _json_payload(obj: dict) -> bytes:
    return json.dumps(obj, separators=(',', ':')).encode('utf-8')


class TestFormatEvent:
    def test_format_transcript_line(self):
        result = format_transcript_line(
            'connected to /tmp/elemctl.sock',
            now=datetime.datetime(2026, 3, 12, 6, 2, 12, 544000),
        )
        assert result == '[2026-03-12 06:02:12.544] connected to /tmp/elemctl.sock'

    def test_snapshot_idle(self):
        msg = {
            'type': 'event', 'event': 'snapshot',
            'online_count': 2, 'expected_count': 3,
            'session': None,
            'devices': [
                {'device_uid': 'sim-1', 'strip': 'main', 'length': 10, 'connected': True},
                {'device_uid': 'sim-2', 'strip': 'aux', 'length': 20, 'connected': True},
                {'device_uid': 'sim-3', 'strip': 'back', 'length': 30, 'connected': False},
            ],
        }
        result = format_event(KIND_JSON, _json_payload(msg))
        assert result == [
            'controller is idle',
            'devices online:',
            '  sim-1: strip "main" with 10 LEDs',
            '  sim-2: strip "aux" with 20 LEDs',
        ]

    def test_snapshot_with_session(self):
        msg = {
            'type': 'event', 'event': 'snapshot',
            'online_count': 1, 'expected_count': 2,
            'session': {
                'playback_state': 'playing',
                'session_id': 5,
                'current_t_rel': 1.25,
                'duration': 8.0,
            },
            'devices': [
                {'device_uid': 'sim-1', 'strip': 'main', 'length': 10, 'connected': True},
                {'device_uid': 'sim-2', 'strip': 'aux', 'length': 20, 'connected': False},
            ],
        }
        result = format_event(KIND_JSON, _json_payload(msg))
        assert result == [
            'playing session=5 t=1.25/8.00s',
            'devices online:',
            '  sim-1: strip "main" with 10 LEDs',
        ]

    def test_reply_message_shortcut(self):
        msg = {
            'type': 'reply',
            'id': 7,
            'ok': True,
            'result': {'message': 'added device sim-2'},
        }
        assert format_event(KIND_JSON, _json_payload(msg)) == ['added device sim-2']

    def test_reply_snapshot_formats_like_controller_event(self):
        msg = {
            'type': 'reply',
            'id': 8,
            'ok': True,
            'result': {
                'event': 'snapshot',
                'session': None,
                'devices': [
                    {'device_uid': 'sim-1', 'strip': 'main', 'length': 10, 'connected': True},
                    {'device_uid': 'sim-2', 'strip': 'aux', 'length': 20, 'connected': False},
                ],
            },
        }
        assert format_event(KIND_JSON, _json_payload(msg)) == [
            'controller is idle',
            'devices online:',
            '  sim-1: strip "main" with 10 LEDs',
        ]

    def test_state_event(self):
        msg = {
            'type': 'event', 'event': 'state',
            'state': 'playing', 'epoch': 1, 'session_id': 3,
        }
        result = format_event(KIND_JSON, _json_payload(msg))
        assert result == ['state playing epoch=1 session=3']

    def test_device_connected(self):
        msg = {
            'type': 'event', 'event': 'device_status',
            'device_uid': 'sim-1', 'strip': 'strip_a', 'length': 10, 'connected': True,
        }
        result = format_event(KIND_JSON, _json_payload(msg))
        assert result == ['device sim-1 connected (strip_a, 10 LEDs)']

    def test_device_disconnected(self):
        msg = {
            'type': 'event', 'event': 'device_status',
            'device_uid': 'sim-2', 'strip': 'strip_b', 'length': 20, 'connected': False,
        }
        result = format_event(KIND_JSON, _json_payload(msg))
        assert result == ['device sim-2 disconnected (strip_b, 20 LEDs)']

    def test_session_start(self):
        msg = {
            'type': 'event', 'event': 'session_start',
            'session_id': 1, 'epoch': 0, 'duration': 2.0,
            'strips': [{'name': 'strip_a'}, {'name': 'strip_b'}],
        }
        result = format_event(KIND_JSON, _json_payload(msg))
        assert result == ['session 1 started epoch=0 duration=2.0s strips=[strip_a, strip_b]']

    def test_loop(self):
        msg = {
            'type': 'event', 'event': 'loop',
            'epoch': 3, 'session_id': 1,
        }
        result = format_event(KIND_JSON, _json_payload(msg))
        assert result == ['loop epoch=3 session=1']

    def test_error(self):
        msg = {
            'type': 'event', 'event': 'error',
            'message': 'something broke',
        }
        result = format_event(KIND_JSON, _json_payload(msg))
        assert result == ['error: something broke']

    def test_programs_updated_is_silent(self):
        msg = {
            'type': 'event',
            'event': 'programs_updated',
            'programs': [{'program_id': 'demo', 'beat': 1.0, 'duration': 2.0, 'error': None}],
        }
        result = format_event(KIND_JSON, _json_payload(msg))
        assert result == []

    def test_reply_programs_formats_catalog(self):
        msg = {
            'type': 'reply',
            'id': 9,
            'ok': True,
            'result': {
                'programs': [
                    {'program_id': 'ambient', 'beat': 1.0, 'duration': 12.0, 'error': None},
                    {'program_id': 'broken', 'beat': None, 'duration': None, 'error': 'missing BEAT'},
                ],
            },
        }
        assert format_event(KIND_JSON, _json_payload(msg)) == [
            '2 program(s) in library:',
            '  #1  ambient          beat=1.0 duration=12.0',
            '  #2  broken           ERROR: missing BEAT',
        ]

    def test_reply_program_formats_publish_result(self):
        msg = {
            'type': 'reply',
            'id': 10,
            'ok': True,
            'result': {
                'program': {
                    'program_id': 'ambient',
                    'beat': 1.0,
                    'duration': 12.0,
                    'error': None,
                },
            },
        }
        assert format_event(KIND_JSON, _json_payload(msg)) == [
            'published program ambient beat=1.0 duration=12.0'
        ]

    def test_reply_ok(self):
        msg = {'type': 'reply', 'id': 7, 'ok': True, 'result': {'x': 1}}
        result = format_event(KIND_JSON, _json_payload(msg))
        assert result == ['reply 7 ok {"x":1}']

    def test_reply_error(self):
        msg = {'type': 'reply', 'id': 3, 'ok': False, 'error': 'bad cmd'}
        result = format_event(KIND_JSON, _json_payload(msg))
        assert result == ['reply 3 ERROR: bad cmd']

    def test_frame_returns_none(self):
        assert format_event(KIND_FRAME, b'\x00' * 20) is None

    def test_unknown_event(self):
        msg = {'type': 'event', 'event': 'future_thing', 'data': 42}
        result = format_event(KIND_JSON, _json_payload(msg))
        assert result == ['unknown event {"type":"event","event":"future_thing","data":42}']

    def test_unknown_kind(self):
        result = format_event(0xFF, b'hello')
        assert result == ['unknown message kind=255 len=5']

    def test_unknown_msg_type(self):
        msg = {'type': 'something_else', 'x': 1}
        result = format_event(KIND_JSON, _json_payload(msg))
        assert result == ['unknown message {"type":"something_else","x":1}']

    def test_bad_json(self):
        result = format_event(KIND_JSON, b'\xff\xfe')
        assert result == ['bad json message len=2']


# ---------------------------------------------------------------------------
# parse_command tests
# ---------------------------------------------------------------------------

class TestParseCommand:
    def test_status(self):
        cmd, err = parse_command('/status', 1)
        assert cmd == {'cmd': 'status', 'id': 1}
        assert err is None

    def test_play(self):
        cmd, err = parse_command('/play', 5)
        assert cmd == {'cmd': 'play', 'id': 5}
        assert err is None

    def test_pause(self):
        cmd, err = parse_command('/pause', 2)
        assert cmd == {'cmd': 'pause', 'id': 2}

    def test_stop(self):
        cmd, err = parse_command('/stop', 3)
        assert cmd == {'cmd': 'stop', 'id': 3}

    def test_shutdown(self):
        cmd, err = parse_command('/shutdown', 4)
        assert cmd == {'cmd': 'shutdown', 'id': 4}

    def test_quit(self):
        cmd, err = parse_command('/quit', 1)
        assert cmd is _QUIT_SENTINEL
        assert err is None

    def test_exit_alias(self):
        cmd, err = parse_command('/exit', 1)
        assert cmd is _QUIT_SENTINEL
        assert err is None

    def test_help(self):
        cmd, err = parse_command('/help', 1)
        assert cmd is _HELP_SENTINEL
        assert err is None

    def test_devices(self):
        cmd, err = parse_command('/devices', 1)
        assert cmd is _DEVICES_SENTINEL
        assert err is None

    def test_programs(self):
        cmd, err = parse_command('/programs', 1)
        assert cmd is _PROGRAMS_SENTINEL
        assert err is None

    def test_session(self):
        cmd, err = parse_command('/session', 1)
        assert cmd is _SESSION_SENTINEL
        assert err is None

    def test_newdevice(self):
        cmd, err = parse_command('/newdevice', 1)
        assert cmd is _NEWDEVICE_SENTINEL
        assert err is None

    def test_rmdevice(self):
        cmd, err = parse_command('/rmdevice sim-1', 1)
        assert cmd == {'cmd': 'rmdevice', 'device_uid': 'sim-1'}
        assert err is None

    def test_unknown_command(self):
        cmd, err = parse_command('/foo', 1)
        assert cmd is None
        assert 'unknown command' in err

    def test_empty_input(self):
        cmd, err = parse_command('', 1)
        assert cmd is None
        assert err is None

    def test_whitespace_only(self):
        cmd, err = parse_command('   ', 1)
        assert cmd is None
        assert err is None

    def test_no_slash_prefix(self):
        cmd, err = parse_command('status', 1)
        assert cmd is None
        assert err is not None

    def test_case_insensitive(self):
        cmd, err = parse_command('/STATUS', 1)
        assert cmd == {'cmd': 'status', 'id': 1}

    def test_leading_whitespace(self):
        cmd, err = parse_command('  /play  ', 3)
        assert cmd == {'cmd': 'play', 'id': 3}

    def test_id_increments(self):
        cmd1, _ = parse_command('/status', 10)
        cmd2, _ = parse_command('/play', 11)
        assert cmd1['id'] == 10
        assert cmd2['id'] == 11

    def test_bare_slash(self):
        cmd, err = parse_command('/', 1)
        assert cmd is None
        assert 'empty command' in err

    def test_slash_whitespace(self):
        cmd, err = parse_command('/   ', 1)
        assert cmd is None
        assert 'empty command' in err

    def test_extra_args_rejected(self):
        cmd, err = parse_command('/status foo', 1)
        assert cmd is None
        assert 'does not take arguments' in err

    def test_shutdown_extra_args_rejected(self):
        cmd, err = parse_command('/shutdown now', 1)
        assert cmd is None
        assert 'does not take arguments' in err

    def test_quit_extra_args_rejected(self):
        cmd, err = parse_command('/quit please', 1)
        assert cmd is None
        assert 'does not take arguments' in err

    def test_help_extra_args_rejected(self):
        cmd, err = parse_command('/help now', 1)
        assert cmd is None
        assert 'does not take arguments' in err

    # /rescan
    def test_rescan(self):
        cmd, err = parse_command('/rescan', 1)
        assert cmd is _RESCAN_SENTINEL
        assert err is None

    def test_rescan_extra_args_rejected(self):
        cmd, err = parse_command('/rescan foo', 1)
        assert cmd is None
        assert 'does not take arguments' in err

    def test_programs_extra_args_rejected(self):
        cmd, err = parse_command('/programs foo', 1)
        assert cmd is None
        assert 'does not take arguments' in err

    # /publish
    def test_publish_by_path(self):
        cmd, err = parse_command('/publish animations/ambient.py', 4)
        assert err is None
        assert cmd == {
            'cmd': 'publish',
            'path': 'animations/ambient.py',
            'program_id': 'ambient',
            'id': 4,
        }

    def test_publish_with_explicit_name(self):
        cmd, err = parse_command('/publish /tmp/draft.py as ambient_bg', 7)
        assert err is None
        assert cmd == {
            'cmd': 'publish',
            'path': '/tmp/draft.py',
            'program_id': 'ambient_bg',
            'id': 7,
        }

    def test_publish_requires_path(self):
        cmd, err = parse_command('/publish', 1)
        assert cmd is None
        assert 'requires a file path' in err

    def test_publish_invalid_syntax(self):
        cmd, err = parse_command('/publish demo.py ambient', 1)
        assert cmd is None
        assert 'usage' in err

    # /load
    def test_load_by_name(self):
        cmd, err = parse_command('/load spark_demo', 5)
        assert err is None
        assert cmd == {
            'cmd': 'load', 'target': 'spark_demo',
            'index': None, 'loop': False, 'id': 5,
        }

    def test_load_by_index(self):
        cmd, err = parse_command('/load #3', 1)
        assert err is None
        assert cmd == {
            'cmd': 'load', 'target': None,
            'index': 3, 'loop': False, 'id': 1,
        }

    def test_load_with_loop(self):
        cmd, err = parse_command('/load #3 loop', 2)
        assert err is None
        assert cmd['loop'] is True
        assert cmd['index'] == 3

    def test_load_name_with_loop(self):
        cmd, err = parse_command('/load spark_demo loop', 1)
        assert err is None
        assert cmd['target'] == 'spark_demo'
        assert cmd['loop'] is True

    def test_load_no_target(self):
        cmd, err = parse_command('/load', 1)
        assert cmd is None
        assert 'requires a target' in err

    def test_load_keyword_args_rejected(self):
        cmd, err = parse_command('/load spark_demo beat=0.5', 1)
        assert cmd is None
        assert 'unexpected arguments' in err

    def test_load_index_zero(self):
        cmd, err = parse_command('/load #0', 1)
        assert cmd is None
        assert 'must be >= 1' in err

    def test_load_index_negative(self):
        cmd, err = parse_command('/load #-1', 1)
        assert cmd is None
        # -1 is parsed by int() but fails the >= 1 check
        assert 'must be >= 1' in err

    def test_load_index_not_int(self):
        cmd, err = parse_command('/load #abc', 1)
        assert cmd is None
        assert 'invalid index' in err

    def test_seek(self):
        cmd, err = parse_command('/seek 12.5', 9)
        assert err is None
        assert cmd == {'cmd': 'seek', 't_rel': 12.5, 'id': 9}

    def test_seek_requires_value(self):
        cmd, err = parse_command('/seek', 1)
        assert cmd is None
        assert 'requires a time' in err

    def test_seek_rejects_bad_value(self):
        cmd, err = parse_command('/seek nope', 1)
        assert cmd is None
        assert 'invalid time' in err

    def test_seek_rejects_negative_value(self):
        cmd, err = parse_command('/seek -1', 1)
        assert cmd is None
        assert '>= 0' in err

    def test_rmdevice_requires_uid(self):
        cmd, err = parse_command('/rmdevice', 1)
        assert cmd is None
        assert 'requires a device uid' in err


class TestNewDeviceValidation:
    def test_validate_device_uid(self):
        assert validate_device_uid('sim-1') is None
        assert validate_device_uid('AA:BB:CC:DD:EE:FF') is None
        assert validate_device_uid('bad uid')

    def test_validate_strip_id(self):
        assert validate_strip_id('main_left') is None
        assert validate_strip_id('main-left') is None
        assert validate_strip_id('main left')

    def test_parse_length(self):
        assert parse_length('60') == (60, None)
        assert parse_length('0')[1] is not None
        assert parse_length('abc')[1] is not None

    def test_parse_t_rel(self):
        assert parse_t_rel('1.5') == (1.5, None)
        assert parse_t_rel('-1')[1] is not None
        assert parse_t_rel('abc')[1] is not None


# ---------------------------------------------------------------------------
# UdsClient.recv_once smoke test (socketpair, no threading)
# ---------------------------------------------------------------------------

def _make_client_pair():
    """Create a (write_sock, UdsClient) pair over AF_UNIX socketpair."""
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    client = UdsClient.__new__(UdsClient)
    client._sock = b
    client._reader = UdsReader()
    return a, b, client


class TestTuiReconnect:
    def test_reader_loop_reconnects_after_disconnect(self, monkeypatch, tmp_path):
        created: list[object] = []
        logs: list[str] = []

        class FakeClient:
            def __init__(self, socket_path: str):
                self.socket_path = socket_path
                self.index = len(created)
                self.closed = False
                created.append(self)

            def fileno(self):
                return self

            def recv_once(self):
                if self.index == 0:
                    raise ConnectionError('server closed connection')
                raise AssertionError('second client should not be read before shutdown')

            def close(self):
                self.closed = True

        def fake_select(read, _write, _exc, _timeout):
            return read, [], []

        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(Path(tmp_path) / 'tui.log'),
        )

        def capture(line: str) -> None:
            logs.append(line)
            if sum('connected to /tmp/elemctl.sock' in item for item in logs) >= 2:
                app._shutdown.set()

        monkeypatch.setattr('elemctl.tui.UdsClient', FakeClient)
        monkeypatch.setattr('elemctl.tui.select.select', fake_select)
        monkeypatch.setattr(app, '_enqueue_log', capture)

        try:
            app._reader_loop()
        finally:
            app._clear_client()
            if app._log_fp is not None:
                app._log_fp.close()

        assert any('connected to /tmp/elemctl.sock' in line for line in logs)
        assert any('disconnected from /tmp/elemctl.sock' in line for line in logs)
        assert sum('connected to /tmp/elemctl.sock' in line for line in logs) == 2


class TestBufferedConnectSnapshot:
    def test_reader_loop_drains_buffered_snapshot_without_select(self, monkeypatch, tmp_path):
        logs: list[str] = []
        updates: list[object] = []

        class FakeClient:
            def __init__(self, socket_path: str):
                self.socket_path = socket_path
                self._pending = True

            def fileno(self):
                raise AssertionError('select should not run while buffered messages exist')

            def has_buffered_messages(self):
                return self._pending

            def recv_once(self):
                self._pending = False
                return [(
                    KIND_JSON,
                    encode_json({
                        'type': 'event',
                        'event': 'snapshot',
                        'protocol_version': 2,
                        'online_count': 1,
                        'expected_count': 1,
                        'session': None,
                        'devices': [{
                            'device_id': 1,
                            'device_uid': 'sim-1',
                            'strip': 'main',
                            'length': 60,
                            'device_type': 'sim',
                            'connected': True,
                        }],
                    })[5:],
                )]

            def close(self):
                pass

        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(Path(tmp_path) / 'tui.log'),
        )

        def capture_log(line: str) -> None:
            logs.append(line)

        def capture_update(update: object) -> None:
            updates.append(update)
            if isinstance(update, PanelSnapshotUpdate):
                app._shutdown.set()

        monkeypatch.setattr('elemctl.tui.UdsClient', FakeClient)
        monkeypatch.setattr(app, '_enqueue_log', capture_log)
        monkeypatch.setattr(app, '_enqueue_panel_update', capture_update)

        try:
            app._reader_loop()
        finally:
            app._clear_client()
            if app._log_fp is not None:
                app._log_fp.close()

        assert any('connected to /tmp/elemctl.sock' in line for line in logs)
        assert any('devices online:' in line for line in logs)
        assert any('sim-1' in line for line in logs)
        assert any(isinstance(update, PanelSnapshotUpdate) for update in updates)


class TestDevicePanel:
    def test_panel_starts_empty_without_snapshot(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        try:
            assert app._device_panel == {}
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_input_prompt_includes_arrow_prefix(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        try:
            assert app._input_prompt.text == '> '
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_decode_snapshot_produces_panel_update(self):
        msg = {
            'type': 'event',
            'event': 'snapshot',
            'session': None,
            'devices': [
                {
                    'device_id': 1,
                    'device_uid': 'sim-1',
                    'device_type': 'sim',
                    'strip': 'main',
                    'length': 10,
                    'connected': True,
                },
                {
                    'device_id': 2,
                    'device_uid': 'sim-2',
                    'device_type': 'esp32',
                    'strip': 'aux',
                    'length': 20,
                    'connected': False,
                },
            ],
        }
        lines, updates = _decode_tui_message(KIND_JSON, _json_payload(msg))
        assert lines == [
            'controller is idle',
            'devices online:',
            '  sim-1: strip "main" with 10 LEDs',
        ]
        assert updates == [
            PanelSnapshotUpdate([
                PanelDeviceInfo('sim-1', 'main', 10, True, 1, 'sim'),
                PanelDeviceInfo('sim-2', 'aux', 20, False, 2, 'esp32'),
            ]),
            DeviceCatalogSnapshotUpdate([
                DeviceCatalogEntry(1, 'sim-1', 'sim', 'main', 10, True),
                DeviceCatalogEntry(2, 'sim-2', 'esp32', 'aux', 20, False),
            ]),
            ProgramCatalogUpdate([]),
            SessionStateUpdate(mode='clear', session=None),
        ]

    def test_decode_snapshot_updates_program_catalog(self):
        msg = {
            'type': 'event',
            'event': 'snapshot',
            'session': None,
            'devices': [],
            'programs': [
                {'program_id': 'ambient', 'beat': 1.0, 'duration': 8.0, 'error': None},
                {'program_id': 'broken', 'beat': None, 'duration': None, 'error': 'missing DURATION'},
            ],
        }
        lines, updates = _decode_tui_message(KIND_JSON, _json_payload(msg))
        assert lines == [
            'controller is idle',
            'devices online: none',
        ]
        assert updates == [
            PanelSnapshotUpdate([]),
            DeviceCatalogSnapshotUpdate([]),
            ProgramCatalogUpdate([
                ProgramCatalogEntry('ambient', 1.0, 8.0, None),
                ProgramCatalogEntry('broken', None, None, 'missing DURATION'),
            ]),
            SessionStateUpdate(mode='clear', session=None),
        ]

    def test_decode_snapshot_updates_session_state(self):
        msg = {
            'type': 'event',
            'event': 'snapshot',
            'session': {
                'session_id': 5,
                'epoch': 2,
                'playback_state': 'playing',
                'current_t_rel': 1.25,
                'duration': 8.0,
                'safe_intervals': [[0.0, 0.5]],
                'strips': [{'name': 'main', 'length': 10}],
            },
            'devices': [],
            'programs': [],
        }
        _lines, updates = _decode_tui_message(KIND_JSON, _json_payload(msg))
        assert updates[-1] == SessionStateUpdate(
            mode='replace',
            session=SessionInfo(
                session_id=5,
                playback_state='playing',
                epoch=2,
                current_t_rel=1.25,
                duration=8.0,
                safe_intervals=[(0.0, 0.5)],
                strips=[SessionStripEntry('main', 10)],
            ),
        )

    def test_decode_state_event_updates_session_state(self):
        msg = {
            'type': 'event',
            'event': 'state',
            'state': 'paused',
            'epoch': 3,
            'session_id': 5,
        }
        lines, updates = _decode_tui_message(KIND_JSON, _json_payload(msg))
        assert lines == ['state paused epoch=3 session=5']
        assert updates == [
            SessionStateUpdate(
                mode='merge',
                session=SessionInfo(
                    session_id=5,
                    playback_state='paused',
                    epoch=3,
                ),
            ),
        ]

    def test_programs_updated_produces_catalog_update_without_transcript(self):
        msg = {
            'type': 'event',
            'event': 'programs_updated',
            'programs': [
                {'program_id': 'ambient', 'beat': 1.0, 'duration': 8.0, 'error': None},
            ],
        }
        lines, updates = _decode_tui_message(KIND_JSON, _json_payload(msg))
        assert lines == []
        assert updates == [
            ProgramCatalogUpdate([ProgramCatalogEntry('ambient', 1.0, 8.0, None)])
        ]

    def test_connected_snapshot_bootstraps_panel(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        try:
            app._apply_panel_update(PanelSnapshotUpdate([
                PanelDeviceInfo('sim-1', 'main', 60, True),
                PanelDeviceInfo('sim-2', 'aux', 30, False),
            ]))
            assert app._device_panel['sim-1'].status == 'connected'
            assert app._device_panel['sim-2'].status == 'configured'
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_device_catalog_snapshot_replaces_catalog(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        try:
            app._apply_panel_update(DeviceCatalogSnapshotUpdate([
                DeviceCatalogEntry(1, 'sim-1', 'sim', 'main', 60, True),
                DeviceCatalogEntry(2, 'sim-2', 'esp32', 'aux', 30, False),
            ]))
            assert app._device_catalog == [
                DeviceCatalogEntry(1, 'sim-1', 'sim', 'main', 60, True),
                DeviceCatalogEntry(2, 'sim-2', 'esp32', 'aux', 30, False),
            ]
            assert app._device_catalog_ready is True
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_program_catalog_update_replaces_catalog(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        try:
            app._apply_panel_update(ProgramCatalogUpdate([
                ProgramCatalogEntry('ambient', 1.0, 8.0, None),
                ProgramCatalogEntry('broken', None, None, 'missing DURATION'),
            ]))
            assert app._program_catalog == [
                ProgramCatalogEntry('ambient', 1.0, 8.0, None),
                ProgramCatalogEntry('broken', None, None, 'missing DURATION'),
            ]
            assert app._program_catalog_ready is True
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_session_state_update_replaces_session(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        try:
            app._apply_panel_update(SessionStateUpdate(
                mode='replace',
                session=SessionInfo(
                    session_id=7,
                    playback_state='loaded',
                    epoch=0,
                    current_t_rel=0.0,
                    duration=12.0,
                    safe_intervals=[(0.0, 0.0)],
                    strips=[SessionStripEntry('main', 60)],
                ),
            ))
            assert app._session == SessionInfo(
                session_id=7,
                playback_state='loaded',
                epoch=0,
                current_t_rel=0.0,
                duration=12.0,
                safe_intervals=[(0.0, 0.0)],
                strips=[SessionStripEntry('main', 60)],
            )
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_controller_disconnect_clears_session(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        try:
            app._apply_panel_update(SessionStateUpdate(
                mode='replace',
                session=SessionInfo(session_id=7, playback_state='playing'),
            ))
            app._apply_panel_update(ControllerConnectionUpdate(True))
            app._apply_panel_update(ControllerConnectionUpdate(False))
            assert app._session is None
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_controller_disconnect_clears_program_catalog_ready(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        try:
            app._apply_panel_update(ControllerConnectionUpdate(True))
            app._apply_panel_update(ProgramCatalogUpdate([
                ProgramCatalogEntry('ambient', 1.0, 8.0, None),
            ]))
            app._apply_panel_update(ControllerConnectionUpdate(False))
            assert app._program_catalog_ready is False
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_controller_disconnect_clears_device_catalog_ready(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        try:
            app._apply_panel_update(ControllerConnectionUpdate(True))
            app._apply_panel_update(DeviceCatalogSnapshotUpdate([
                DeviceCatalogEntry(1, 'sim-1', 'sim', 'main', 60, True),
            ]))
            app._apply_panel_update(ControllerConnectionUpdate(False))
            assert app._device_catalog_ready is False
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_device_disconnect_sets_offline_timestamp(self, monkeypatch, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        try:
            app._apply_panel_update(PanelSnapshotUpdate([
                PanelDeviceInfo('sim-1', 'main', 60, True),
            ]))
            monkeypatch.setattr('elemctl.tui.time.monotonic_ns', lambda: 123_000_000_000)
            app._apply_panel_update(PanelDeviceStatusUpdate(
                PanelDeviceInfo('sim-1', 'main', 60, False)
            ))
            assert app._device_panel['sim-1'].status == 'offline'
            assert app._device_panel['sim-1'].disconnected_at_ns == 123_000_000_000
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_controller_disconnect_is_separate_from_device_state(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        try:
            app._apply_panel_update(PanelSnapshotUpdate([
                PanelDeviceInfo('sim-1', 'main', 60, True),
            ]))
            app._apply_panel_update(ControllerConnectionUpdate(False))
            assert app._controller_connected is False
            assert app._device_panel['sim-1'].status == 'connected'
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_panel_summary_renders_connected_controller_and_device_count(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        try:
            app._controller_connected = True
            app._controller_disconnected_at_ns = None
            app._device_panel = {
                'sim-1': DevicePanelEntry(
                    device_uid='sim-1',
                    strip_id='main',
                    length=60,
                    status='connected',
                ),
                'sim-2': DevicePanelEntry(
                    device_uid='sim-2',
                    strip_id='aux',
                    length=30,
                    status='offline',
                    disconnected_at_ns=0,
                ),
            }
            text = ''.join(fragment[1] for fragment in app._render_panel_summary())
            assert 'controller' in text
            assert 'devices' in text
            assert '1' in text
            assert 'online' not in text
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_panel_summary_renders_controller_offline_age(self, monkeypatch, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        try:
            app._controller_connected = False
            app._controller_disconnected_at_ns = 0
            monkeypatch.setattr('elemctl.tui.time.monotonic_ns', lambda: 65_000_000_000)
            text = ''.join(fragment[1] for fragment in app._render_panel_summary())
            assert 'controller' in text
            assert '1m' in text
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_panel_age_uses_now_for_first_minute(self, monkeypatch, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        try:
            monkeypatch.setattr('elemctl.tui.time.monotonic_ns', lambda: 59_000_000_000)
            assert app._format_panel_age(0) == 'now'
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_panel_age_uses_hours_and_days(self, monkeypatch, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        try:
            monkeypatch.setattr('elemctl.tui.time.monotonic_ns', lambda: 3_600_000_000_000)
            assert app._format_panel_age(0) == '1h'
            monkeypatch.setattr('elemctl.tui.time.monotonic_ns', lambda: 86_400_000_000_000)
            assert app._format_panel_age(0) == '1d'
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_panel_rows_render_offline_badge(self, monkeypatch, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        try:
            monkeypatch.setattr('elemctl.tui.time.monotonic_ns', lambda: 59_000_000_000)
            app._device_panel = {
                'sim-1': DevicePanelEntry(
                    device_uid='sim-1',
                    strip_id='main',
                    length=60,
                    status='offline',
                    disconnected_at_ns=0,
                )
            }
            fragments = app._render_panel_rows()
            text = ''.join(fragment[1] for fragment in fragments)
            assert 'sim-1' in text
            assert 'main' in text
            assert '60' in text
            assert any(
                style == 'class:panel.badge.offline' and text.strip() == 'now'
                for style, text in fragments
            )
            assert any(
                style == 'class:panel.length' and text == '60 '
                for style, text in fragments
            )
        finally:
            if app._log_fp is not None:
                app._log_fp.close()


class TestNewDeviceDialog:
    class _FakeClient:
        def __init__(self):
            self.commands: list[dict] = []
            self.closed = False

        def send_cmd(self, cmd: dict) -> None:
            self.commands.append(cmd)

        def close(self) -> None:
            self.closed = True

    def test_newdevice_dialog_opens_and_closes(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        app._set_client(self._FakeClient())

        try:
            app._start_newdevice()
            assert app._newdevice_dialog is not None
            assert len(app._root_container.floats) == 1
            app._cancel_newdevice_dialog()
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert app._newdevice_dialog is None
        assert app._root_container.floats == []

    def test_newdevice_dialog_sends_add_device_command_without_local_config_read(
        self, tmp_path
    ):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)

        try:
            app._start_newdevice()
            state = app._newdevice_dialog
            assert state is not None
            state.device_type.current_value = 'sim'
            state.device_uid.text = 'sim-1'
            state.strip_id.text = 'main'
            state.length.text = '60'
            app._submit_newdevice_dialog()
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert app._newdevice_dialog is not None
        assert app._newdevice_dialog.pending_request_id == 1
        assert client.commands == [{
            'cmd': 'add_device',
            'device_uid': 'sim-1',
            'device_type': 'sim',
            'strip_id': 'main',
            'length': 60,
            'id': 1,
        }]

    def test_newdevice_dialog_success_reply_closes(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)

        try:
            app._start_newdevice()
            state = app._newdevice_dialog
            assert state is not None
            state.device_type.current_value = 'sim'
            state.device_uid.text = 'sim-1'
            state.strip_id.text = 'main'
            state.length.text = '60'
            app._submit_newdevice_dialog()
            app._apply_panel_update(CommandReplyUpdate(reply_id=1, ok=True, error=None))
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert app._newdevice_dialog is None

    def test_newdevice_dialog_error_reply_stays_open(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)

        try:
            app._start_newdevice()
            state = app._newdevice_dialog
            assert state is not None
            state.device_type.current_value = 'sim'
            state.device_uid.text = 'sim-1'
            state.strip_id.text = 'main'
            state.length.text = '60'
            app._submit_newdevice_dialog()
            app._apply_panel_update(CommandReplyUpdate(
                reply_id=1,
                ok=False,
                error='device uid already exists: sim-1',
            ))
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert app._newdevice_dialog is not None
        assert app._newdevice_dialog.pending_request_id is None
        assert app._newdevice_dialog.error_text == 'device uid already exists: sim-1'

    def test_newdevice_dialog_ignores_double_submit_while_pending(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)

        try:
            app._start_newdevice()
            state = app._newdevice_dialog
            assert state is not None
            state.device_type.current_value = 'sim'
            state.device_uid.text = 'sim-1'
            state.strip_id.text = 'main'
            state.length.text = '60'
            app._submit_newdevice_dialog()
            app._submit_newdevice_dialog()
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert len(client.commands) == 1

    def test_newdevice_dialog_validation_error_stays_open(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        app._set_client(self._FakeClient())

        try:
            app._start_newdevice()
            state = app._newdevice_dialog
            assert state is not None
            state.device_uid.text = 'bad uid'
            state.strip_id.text = 'main'
            state.length.text = '60'
            app._submit_newdevice_dialog()
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert app._newdevice_dialog is not None
        assert app._newdevice_dialog.error_text == (
            'device uid may only contain letters, numbers, ., _, -, and :'
        )

    def test_newdevice_dialog_send_failure_stays_open(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )

        class FailingClient(self._FakeClient):
            def send_cmd(self, cmd: dict) -> None:
                raise OSError('broken pipe')

        app._set_client(FailingClient())

        try:
            app._start_newdevice()
            state = app._newdevice_dialog
            assert state is not None
            state.device_uid.text = 'sim-1'
            state.strip_id.text = 'main'
            state.length.text = '60'
            app._submit_newdevice_dialog()
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert app._newdevice_dialog is not None
        assert app._newdevice_dialog.error_text == 'send failed: broken pipe'
        assert app._newdevice_dialog.pending_request_id is None

    def test_rmdevice_sends_remove_device_command(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)

        try:
            app._do_rmdevice('sim-1')
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert client.commands == [{
            'cmd': 'remove_device',
            'device_uid': 'sim-1',
            'id': 1,
        }]

    def test_devices_opens_manager_dialog_from_catalog(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)
        app._device_catalog_ready = True
        app._device_catalog = [
            DeviceCatalogEntry(1, 'sim-1', 'sim', 'main', 60, True),
            DeviceCatalogEntry(2, 'sim-2', 'esp32', 'aux', 30, False),
        ]

        try:
            app._do_devices()
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert client.commands == []
        assert isinstance(app._active_modal, DeviceManagerDialogState)
        assert app._active_modal.device_list.current_value == 'sim-1'
        assert len(app._root_container.floats) == 1

    def test_devices_requires_connected_controller(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )

        try:
            app._do_devices()
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert any(line.endswith('controller not connected') for line in app._log_lines)

    def test_devices_without_catalog_logs_waiting_message(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        app._set_client(self._FakeClient())

        try:
            app._do_devices()
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert any(line.endswith('device list not available yet') for line in app._log_lines)

    def test_devices_empty_catalog_opens_empty_manager(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        app._set_client(self._FakeClient())
        app._device_catalog_ready = True

        try:
            app._do_devices()
            state = app._active_modal
            assert isinstance(state, DeviceManagerDialogState)
            assert state.device_list is None
            assert state.add_button is not None
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_empty_device_manager_add_opens_newdevice_dialog(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        app._set_client(self._FakeClient())
        app._device_catalog_ready = True

        try:
            app._do_devices()
            state = app._active_modal
            assert isinstance(state, DeviceManagerDialogState)
            assert state.add_button is not None
            state.add_button.handler()
            assert app._newdevice_dialog is not None
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_device_manager_edit_opens_prefilled_dialog(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        app._set_client(self._FakeClient())
        app._device_catalog_ready = True
        app._device_catalog = [DeviceCatalogEntry(1, 'sim-1', 'sim', 'main', 60, True)]

        try:
            app._do_devices()
            app._start_device_edit()
            state = app._active_modal
            assert isinstance(state, DeviceEditDialogState)
            assert state.target_device_uid == 'sim-1'
            assert state.device_uid.text == 'sim-1'
            assert state.strip_id.text == 'main'
            assert state.length.text == '60'
            assert state.device_type == 'sim'
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_device_edit_sends_edit_device_command(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)
        app._device_catalog_ready = True
        app._device_catalog = [DeviceCatalogEntry(1, 'sim-1', 'sim', 'main', 60, True)]

        try:
            app._do_devices()
            app._start_device_edit()
            state = app._active_modal
            assert isinstance(state, DeviceEditDialogState)
            state.device_uid.text = 'sim-1-fixed'
            state.strip_id.text = 'main_left'
            state.length.text = '100'
            app._submit_device_edit_dialog()
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert client.commands == [{
            'cmd': 'edit_device',
            'target_device_uid': 'sim-1',
            'device_uid': 'sim-1-fixed',
            'strip_id': 'main_left',
            'length': 100,
            'id': 1,
        }]

    def test_device_edit_error_reply_keeps_dialog_open(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)
        app._device_catalog_ready = True
        app._device_catalog = [DeviceCatalogEntry(1, 'sim-1', 'sim', 'main', 60, True)]

        try:
            app._do_devices()
            app._start_device_edit()
            app._submit_device_edit_dialog()
            app._apply_panel_update(CommandReplyUpdate(reply_id=1, ok=False, error='duplicate uid'))
            state = app._active_modal
            assert isinstance(state, DeviceEditDialogState)
            assert state.error_text == 'duplicate uid'
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_device_edit_success_reply_closes_dialog(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)
        app._device_catalog_ready = True
        app._device_catalog = [DeviceCatalogEntry(1, 'sim-1', 'sim', 'main', 60, True)]

        try:
            app._do_devices()
            app._start_device_edit()
            app._submit_device_edit_dialog()
            app._apply_panel_update(CommandReplyUpdate(reply_id=1, ok=True, error=None))
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert app._active_modal is None

    def test_device_remove_sends_remove_device_command(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)
        app._device_catalog_ready = True
        app._device_catalog = [DeviceCatalogEntry(1, 'sim-1', 'sim', 'main', 60, True)]

        try:
            app._do_devices()
            app._start_device_remove()
            state = app._active_modal
            assert isinstance(state, DeviceRemoveDialogState)
            app._submit_device_remove_dialog()
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert client.commands == [{
            'cmd': 'remove_device',
            'device_uid': 'sim-1',
            'id': 1,
        }]

    def test_device_remove_success_reply_closes_dialog(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)
        app._device_catalog_ready = True
        app._device_catalog = [DeviceCatalogEntry(1, 'sim-1', 'sim', 'main', 60, True)]

        try:
            app._do_devices()
            app._start_device_remove()
            app._submit_device_remove_dialog()
            app._apply_panel_update(CommandReplyUpdate(reply_id=1, ok=True, error=None))
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert app._active_modal is None

    def test_newdevice_requires_connected_controller(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )

        try:
            app._start_newdevice()
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert app._newdevice_dialog is None
        assert any('controller not connected' in line for line in app._log_lines)

    def test_on_input_ignored_while_dialog_open(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        app._set_client(self._FakeClient())

        try:
            app._start_newdevice()
            app._input_buffer.text = '/status'
            app._on_input(app._input_buffer)
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert app._newdevice_dialog is not None


class TestProgramCatalogCommands:
    class _FakeClient:
        def __init__(self):
            self.commands: list[dict] = []

        def send_cmd(self, cmd: dict) -> None:
            self.commands.append(cmd)

        def close(self) -> None:
            pass

    def test_rescan_sends_controller_command(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)

        try:
            app._do_rescan()
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert client.commands == [{'cmd': 'rescan_programs', 'id': 1}]

    def test_rescan_requires_connection(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )

        try:
            app._do_rescan()
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert any(line.endswith('not connected') for line in app._log_lines)

    def test_programs_requires_connected_controller(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )

        try:
            app._do_programs()
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert any(line.endswith('controller not connected') for line in app._log_lines)

    def test_programs_without_catalog_ready_logs_waiting_message(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        app._set_client(self._FakeClient())

        try:
            app._do_programs()
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert any(line.endswith('program list not available yet') for line in app._log_lines)

    def test_programs_empty_catalog_opens_empty_manager(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        app._set_client(self._FakeClient())
        app._program_catalog_ready = True

        try:
            app._do_programs()
            state = app._active_modal
            assert isinstance(state, ProgramManagerDialogState)
            assert state.program_list is None
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_programs_nonempty_catalog_opens_manager(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        app._set_client(self._FakeClient())
        app._program_catalog_ready = True
        app._program_catalog = [
            ProgramCatalogEntry('ambient', 1.0, 8.0, None),
            ProgramCatalogEntry('broken', None, None, 'missing DURATION'),
        ]

        try:
            app._do_programs()
            state = app._active_modal
            assert isinstance(state, ProgramManagerDialogState)
            assert state.program_list is not None
            assert state.program_list.current_value == 'ambient'
            assert state.loop_enabled is False
            assert state.loop_toggle_button is not None
            assert state.loop_toggle_button.text == 'Loop: Off'
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_program_manager_rows_show_duration_first(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        app._program_catalog = [ProgramCatalogEntry('ambient', 1.0, 8.0, None)]

        try:
            options = app._program_manager_options()
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert options == [('ambient', 'ambient            8s      beat 1      [ok]')]

    def test_program_manager_detail_text_shows_selected_program_and_loop(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        app._set_client(self._FakeClient())
        app._program_catalog_ready = True
        app._program_catalog = [ProgramCatalogEntry('ambient', 1.0, 8.0, None)]

        try:
            app._do_programs()
            assert app._program_manager_detail_text() == (
                'Selected: ambient   duration: 8s   beat: 1   loop: Off'
            )
            app._toggle_program_loop()
            assert app._program_manager_detail_text() == (
                'Selected: ambient   duration: 8s   beat: 1   loop: On'
            )
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_publish_reads_file_and_sends_publish_program(self, tmp_path):
        path = tmp_path / 'ambient.py'
        path.write_text("BEAT = 1.0\nDURATION = 8.0\n")
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)

        try:
            app._do_publish({
                'cmd': 'publish',
                'path': str(path),
                'program_id': 'ambient',
                'id': 1,
            })
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert client.commands == [{
            'cmd': 'publish_program',
            'program_id': 'ambient',
            'source': "BEAT = 1.0\nDURATION = 8.0\n",
            'id': 1,
        }]
        assert any(line.endswith(f'> /publish {path}') for line in app._log_lines)

    def test_publish_with_explicit_name_uses_name(self, tmp_path):
        path = tmp_path / 'draft.py'
        path.write_text("BEAT = 1.0\nDURATION = 8.0\n")
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)

        try:
            app._do_publish({
                'cmd': 'publish',
                'path': str(path),
                'program_id': 'ambient_bg',
                'id': 1,
            })
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert client.commands == [{
            'cmd': 'publish_program',
            'program_id': 'ambient_bg',
            'source': "BEAT = 1.0\nDURATION = 8.0\n",
            'id': 1,
        }]
        assert any(line.endswith(f'> /publish {path} as ambient_bg') for line in app._log_lines)

    def test_publish_missing_file_is_local_error(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)
        missing = tmp_path / 'missing.py'

        try:
            app._do_publish({
                'cmd': 'publish',
                'path': str(missing),
                'program_id': 'missing',
                'id': 1,
            })
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert client.commands == []
        assert any(
            line.endswith(f'cannot read {missing}: [Errno 2] No such file or directory: \'{missing}\'')
            for line in app._log_lines
        )

    def test_program_manager_load_sends_load_program(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)
        app._program_catalog_ready = True
        app._program_catalog = [ProgramCatalogEntry('ambient', 1.0, 8.0, None)]

        try:
            app._do_programs()
            app._submit_program_load()
            state = app._active_modal
            assert isinstance(state, ProgramManagerDialogState)
            assert state.pending_request_id == 1
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert client.commands == [{
            'cmd': 'load_program',
            'program_id': 'ambient',
            'loop': False,
            'id': 1,
        }]

    def test_program_manager_loop_toggle_changes_load_flag(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)
        app._program_catalog_ready = True
        app._program_catalog = [ProgramCatalogEntry('ambient', 1.0, 8.0, None)]

        try:
            app._do_programs()
            app._toggle_program_loop()
            state = app._active_modal
            assert isinstance(state, ProgramManagerDialogState)
            assert state.loop_enabled is True
            assert state.loop_toggle_button is not None
            assert state.loop_toggle_button.text == 'Loop: On'
            app._submit_program_load()
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert client.commands == [{
            'cmd': 'load_program',
            'program_id': 'ambient',
            'loop': True,
            'id': 1,
        }]

    def test_program_manager_rejects_broken_entry_locally(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)
        app._program_catalog_ready = True
        app._program_catalog = [ProgramCatalogEntry('broken', None, None, 'missing DURATION')]

        try:
            app._do_programs()
            app._submit_program_load()
            state = app._active_modal
            assert isinstance(state, ProgramManagerDialogState)
            assert state.error_text == 'cannot load broken: missing DURATION'
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert client.commands == []

    def test_program_manager_rescan_sends_controller_command(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)
        app._program_catalog_ready = True

        try:
            app._do_programs()
            app._submit_program_rescan()
            state = app._active_modal
            assert isinstance(state, ProgramManagerDialogState)
            assert state.pending_request_id == 1
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert client.commands == [{'cmd': 'rescan_programs', 'id': 1}]

    def test_program_manager_reply_error_stays_inline(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)
        app._program_catalog_ready = True
        app._program_catalog = [ProgramCatalogEntry('ambient', 1.0, 8.0, None)]

        try:
            app._do_programs()
            app._submit_program_load()
            app._apply_panel_update(CommandReplyUpdate(
                reply_id=1,
                ok=False,
                error='no configured devices',
            ))
            state = app._active_modal
            assert isinstance(state, ProgramManagerDialogState)
            assert state.error_text == 'no configured devices'
            assert state.pending_request_id is None
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_program_publish_dialog_sends_publish_program(self, tmp_path):
        path = tmp_path / 'ambient.py'
        path.write_text("BEAT = 1.0\nDURATION = 8.0\n")
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)
        app._program_catalog_ready = True
        app._program_catalog = [ProgramCatalogEntry('ambient', 1.0, 8.0, None)]

        try:
            app._do_programs()
            app._start_program_publish()
            state = app._active_modal
            assert isinstance(state, ProgramPublishDialogState)
            state.path.text = str(path)
            state.program_id.text = 'ambient_bg'
            app._submit_program_publish_dialog()
            assert state.pending_request_id == 1
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert client.commands == [{
            'cmd': 'publish_program',
            'program_id': 'ambient_bg',
            'source': "BEAT = 1.0\nDURATION = 8.0\n",
            'id': 1,
        }]

    def test_program_publish_dialog_file_error_stays_inline(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        app._set_client(self._FakeClient())
        app._program_catalog_ready = True

        try:
            app._do_programs()
            app._start_program_publish()
            state = app._active_modal
            assert isinstance(state, ProgramPublishDialogState)
            state.path.text = str(tmp_path / 'missing.py')
            state.program_id.text = 'missing'
            app._submit_program_publish_dialog()
            assert 'cannot read' in state.error_text
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_program_publish_success_returns_to_manager(self, tmp_path):
        path = tmp_path / 'ambient.py'
        path.write_text("BEAT = 1.0\nDURATION = 8.0\n")
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)
        app._program_catalog_ready = True

        try:
            app._do_programs()
            app._start_program_publish()
            state = app._active_modal
            assert isinstance(state, ProgramPublishDialogState)
            state.path.text = str(path)
            state.program_id.text = 'ambient'
            app._submit_program_publish_dialog()
            app._apply_panel_update(CommandReplyUpdate(reply_id=1, ok=True, error=None))
            assert isinstance(app._active_modal, ProgramManagerDialogState)
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_programs_updated_refreshes_manager_list_while_open(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        app._set_client(self._FakeClient())
        app._program_catalog_ready = True
        app._program_catalog = [ProgramCatalogEntry('ambient', 1.0, 8.0, None)]

        try:
            app._do_programs()
            app._toggle_program_loop()
            app._apply_panel_update(ProgramCatalogUpdate([
                ProgramCatalogEntry('ambient', 1.0, 8.0, None),
                ProgramCatalogEntry('spark_demo', 0.5, 16.0, None),
            ]))
            state = app._active_modal
            assert isinstance(state, ProgramManagerDialogState)
            assert state.program_list is not None
            assert [value for value, _label in state.program_list.values] == [
                'ambient',
                'spark_demo',
            ]
            assert state.loop_enabled is True
            assert state.loop_toggle_button is not None
            assert state.loop_toggle_button.text == 'Loop: On'
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_load_by_name_sends_load_program(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)
        app._program_catalog = [
            ProgramCatalogEntry('ambient', 1.0, 8.0, None),
            ProgramCatalogEntry('broken', None, None, 'missing DURATION'),
        ]
        app._program_catalog_ready = True

        try:
            app._do_load({'cmd': 'load', 'target': 'ambient', 'index': None, 'loop': True, 'id': 1})
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert client.commands == [{
            'cmd': 'load_program',
            'program_id': 'ambient',
            'loop': True,
            'id': 1,
        }]
        assert any(line.endswith('> /load ambient loop') for line in app._log_lines)

    def test_load_by_index_sends_load_program(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)
        app._program_catalog = [
            ProgramCatalogEntry('ambient', 1.0, 8.0, None),
            ProgramCatalogEntry('spark_demo', 0.5, 16.0, None),
        ]
        app._program_catalog_ready = True

        try:
            app._do_load({'cmd': 'load', 'target': None, 'index': 2, 'loop': False, 'id': 1})
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert client.commands == [{
            'cmd': 'load_program',
            'program_id': 'spark_demo',
            'loop': False,
            'id': 1,
        }]
        assert any(line.endswith('> /load spark_demo') for line in app._log_lines)

    def test_load_rejects_broken_program_locally(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)
        app._program_catalog = [
            ProgramCatalogEntry('broken', None, None, 'missing DURATION'),
        ]
        app._program_catalog_ready = True

        try:
            app._do_load({'cmd': 'load', 'target': 'broken', 'index': None, 'loop': False, 'id': 1})
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert client.commands == []
        assert any(
            line.endswith('cannot load broken: missing DURATION')
            for line in app._log_lines
        )

    def test_load_without_catalog_before_snapshot_waits_for_catalog(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)

        try:
            app._do_load({'cmd': 'load', 'target': 'ambient', 'index': None, 'loop': False, 'id': 1})
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert client.commands == []
        assert any(
            line.endswith('program list not available yet')
            for line in app._log_lines
        )

    def test_load_without_catalog_after_snapshot_suggests_rescan(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)
        app._program_catalog_ready = True

        try:
            app._do_load({'cmd': 'load', 'target': 'ambient', 'index': None, 'loop': False, 'id': 1})
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert client.commands == []
        assert any(
            line.endswith('no known programs, try /rescan')
            for line in app._log_lines
        )

    def test_load_index_out_of_range_uses_catalog_size(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)
        app._program_catalog = [ProgramCatalogEntry('ambient', 1.0, 8.0, None)]
        app._program_catalog_ready = True

        try:
            app._do_load({'cmd': 'load', 'target': None, 'index': 2, 'loop': False, 'id': 1})
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert client.commands == []
        assert any(
            line.endswith('index #2 out of range (have 1 programs)')
            for line in app._log_lines
        )


class TestSessionCommands:
    class _FakeClient:
        def __init__(self):
            self.commands: list[dict] = []

        def send_cmd(self, cmd: dict) -> None:
            self.commands.append(cmd)

        def close(self) -> None:
            pass

    def test_session_requires_connected_controller(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )

        try:
            app._do_session()
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert any(line.endswith('controller not connected') for line in app._log_lines)

    def test_session_without_snapshot_logs_waiting_message(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        app._set_client(self._FakeClient())

        try:
            app._do_session()
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert any(line.endswith('session status not available yet') for line in app._log_lines)

    def test_session_empty_opens_manager(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        app._set_client(self._FakeClient())
        app._device_catalog_ready = True

        try:
            app._do_session()
            state = app._active_modal
            assert isinstance(state, SessionManagerDialogState)
            assert state.mode == 'empty'
            assert state.play_button is None
            assert state.programs_button is not None
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_session_active_opens_manager(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        app._set_client(self._FakeClient())
        app._device_catalog_ready = True
        app._session = SessionInfo(
            session_id=5,
            playback_state='loaded',
            epoch=0,
            current_t_rel=0.0,
            duration=8.0,
            safe_intervals=[(0.0, 0.0)],
            strips=[SessionStripEntry('main', 60)],
        )

        try:
            app._do_session()
            state = app._active_modal
            assert isinstance(state, SessionManagerDialogState)
            assert state.mode == 'active'
            assert state.play_button is not None
            assert state.seek_button is not None
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_session_play_sends_play_command(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)
        app._device_catalog_ready = True
        app._session = SessionInfo(session_id=5, playback_state='loaded')

        try:
            app._do_session()
            app._submit_session_command('play')
            state = app._active_modal
            assert isinstance(state, SessionManagerDialogState)
            assert state.pending_request_id == 1
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert client.commands == [{'cmd': 'play', 'id': 1}]

    def test_session_pause_sends_pause_command(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)
        app._device_catalog_ready = True
        app._session = SessionInfo(session_id=5, playback_state='playing')

        try:
            app._do_session()
            app._submit_session_command('pause')
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert client.commands == [{'cmd': 'pause', 'id': 1}]

    def test_session_stop_sends_stop_command(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)
        app._device_catalog_ready = True
        app._session = SessionInfo(session_id=5, playback_state='paused')

        try:
            app._do_session()
            app._submit_session_command('stop')
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert client.commands == [{'cmd': 'stop', 'id': 1}]

    def test_session_seek_dialog_sends_seek_command(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)
        app._device_catalog_ready = True
        app._session = SessionInfo(session_id=5, playback_state='playing', current_t_rel=1.5)

        try:
            app._do_session()
            app._start_session_seek()
            state = app._active_modal
            assert isinstance(state, SessionSeekDialogState)
            state.t_rel.text = '3.25'
            app._submit_session_seek_dialog()
            assert state.pending_request_id == 1
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert client.commands == [{'cmd': 'seek', 't_rel': 3.25, 'id': 1}]

    def test_session_seek_invalid_input_stays_inline(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        app._set_client(self._FakeClient())
        app._device_catalog_ready = True
        app._session = SessionInfo(session_id=5, playback_state='playing')

        try:
            app._do_session()
            app._start_session_seek()
            state = app._active_modal
            assert isinstance(state, SessionSeekDialogState)
            state.t_rel.text = 'nope'
            app._submit_session_seek_dialog()
            assert 'number' in state.error_text
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_session_reply_error_stays_inline(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)
        app._device_catalog_ready = True
        app._session = SessionInfo(session_id=5, playback_state='loaded')

        try:
            app._do_session()
            app._submit_session_command('play')
            app._apply_panel_update(CommandReplyUpdate(
                reply_id=1,
                ok=False,
                error='cannot play while disconnected',
            ))
            state = app._active_modal
            assert isinstance(state, SessionManagerDialogState)
            assert state.error_text == 'cannot play while disconnected'
            assert state.pending_request_id is None
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_session_seek_success_returns_to_manager(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)
        app._device_catalog_ready = True
        app._session = SessionInfo(session_id=5, playback_state='playing')

        try:
            app._do_session()
            app._start_session_seek()
            state = app._active_modal
            assert isinstance(state, SessionSeekDialogState)
            state.t_rel.text = '2.0'
            app._submit_session_seek_dialog()
            app._apply_panel_update(CommandReplyUpdate(reply_id=1, ok=True, error=None))
            assert isinstance(app._active_modal, SessionManagerDialogState)
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_state_updates_refresh_session_manager(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        app._set_client(self._FakeClient())
        app._device_catalog_ready = True
        app._session = SessionInfo(session_id=5, playback_state='loaded', epoch=0)

        try:
            app._do_session()
            app._apply_panel_update(SessionStateUpdate(
                mode='merge',
                session=SessionInfo(session_id=5, playback_state='playing', epoch=1),
            ))
            state = app._active_modal
            assert isinstance(state, SessionManagerDialogState)
            assert app._session is not None
            assert app._session.playback_state == 'playing'
            assert app._session.epoch == 1
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_session_command_routes_seek_from_input(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)

        try:
            app._input_buffer.text = '/seek 4.5'
            app._on_input(app._input_buffer)
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert client.commands == [{'cmd': 'seek', 't_rel': 4.5, 'id': 1}]


class TestTuiMain:
    def test_starts_without_config_file(self, monkeypatch, tmp_path):
        called = {}

        class FakeApp:
            def __init__(self, socket_path, log_file):
                called["socket_path"] = socket_path
                called["log_file"] = log_file

            def run(self):
                called["ran"] = True

        monkeypatch.setattr('elemctl.tui.TuiApp', FakeApp)
        monkeypatch.setattr(
            'sys.argv',
            [
                'elemctl.tui',
                '--log-dir', str(tmp_path / 'logs'),
            ],
        )

        from elemctl.tui import main as tui_main
        tui_main()

        assert called["socket_path"] == '/tmp/elemctl.sock'
        assert called["log_file"] == str(tmp_path / 'logs' / 'tui.log')
        assert called["ran"] is True


class TestRecvOnce:
    def test_recv_single_json_message(self):
        """Write raw wire bytes to one end, recv_once on the other."""
        a, b, client = _make_client_pair()
        try:
            msg = {'type': 'event', 'event': 'snapshot', 'online_count': 1}
            a.sendall(encode_json(msg))

            messages = client.recv_once()
            assert len(messages) == 1
            kind, payload = messages[0]
            assert kind == KIND_JSON
            decoded = json.loads(payload)
            assert decoded['event'] == 'snapshot'
        finally:
            a.close()
            b.close()

    def test_recv_multiple_messages_one_recv(self):
        """Two messages sent together should both be returned."""
        a, b, client = _make_client_pair()
        try:
            a.sendall(encode_json({'a': 1}) + encode_json({'b': 2}))

            messages = client.recv_once()
            assert len(messages) == 2
        finally:
            a.close()
            b.close()

    def test_recv_connection_closed(self):
        """Server closing connection raises ConnectionError."""
        a, b, client = _make_client_pair()
        try:
            a.close()
            with pytest.raises(ConnectionError):
                client.recv_once()
        finally:
            b.close()
