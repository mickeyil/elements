"""Tests for TUI non-UI logic: format_event, parse_command, metadata, scan."""

import datetime
import json
import socket
from pathlib import Path

import pytest

from elemctl.tui import (
    ControllerConnectionUpdate,
    DevicePanelEntry,
    PanelDeviceInfo,
    PanelDeviceStatusUpdate,
    PanelSnapshotUpdate,
    TuiApp,
    _decode_tui_message,
    format_event,
    parse_command,
    extract_metadata,
    scan_animations,
    format_transcript_line,
    parse_length,
    validate_device_uid,
    validate_strip_id,
    _DEVICES_SENTINEL,
    _HELP_SENTINEL,
    _NEWDEVICE_SENTINEL,
    _QUIT_SENTINEL,
    _RESCAN_SENTINEL,
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
            '/tmp/config.json',
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
            '/tmp/config.json',
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
    def test_panel_seeds_from_config(self, tmp_path):
        config_path = tmp_path / 'config.json'
        config_path.write_text(json.dumps({
            'controller': {'frame_port': 9002},
            'devices': [
                {
                    'device_id': 1,
                    'device_uid': 'sim-1',
                    'device_type': 'sim',
                    'host': '',
                    'tcp_port': 0,
                    'strip_id': 'main',
                    'length': 60,
                },
                {
                    'device_id': 2,
                    'device_uid': 'sim-2',
                    'device_type': 'sim',
                    'host': '',
                    'tcp_port': 0,
                    'strip_id': 'aux',
                    'length': 30,
                },
            ],
        }))
        app = TuiApp(
            '/tmp/elemctl.sock',
            str(config_path),
            log_file=str(tmp_path / 'tui.log'),
        )
        try:
            assert list(app._device_panel) == ['sim-1', 'sim-2']
            assert app._device_panel['sim-1'].status == 'configured'
            assert app._device_panel['sim-2'].strip_id == 'aux'
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

    def test_decode_snapshot_produces_panel_update(self):
        msg = {
            'type': 'event',
            'event': 'snapshot',
            'session': None,
            'devices': [
                {'device_uid': 'sim-1', 'strip': 'main', 'length': 10, 'connected': True},
                {'device_uid': 'sim-2', 'strip': 'aux', 'length': 20, 'connected': False},
            ],
        }
        lines, updates = _decode_tui_message(KIND_JSON, _json_payload(msg))
        assert lines == [
            'controller is idle',
            'devices online:',
            '  sim-1: strip "main" with 10 LEDs',
        ]
        assert updates == [PanelSnapshotUpdate([
            PanelDeviceInfo('sim-1', 'main', 10, True),
            PanelDeviceInfo('sim-2', 'aux', 20, False),
        ])]

    def test_connected_snapshot_bootstraps_panel(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            str(tmp_path / 'config.json'),
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

    def test_device_disconnect_sets_offline_timestamp(self, monkeypatch, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            str(tmp_path / 'config.json'),
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
            str(tmp_path / 'config.json'),
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
            str(tmp_path / 'config.json'),
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
            str(tmp_path / 'config.json'),
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

    def test_panel_rows_render_offline_badge(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            str(tmp_path / 'config.json'),
            log_file=str(tmp_path / 'tui.log'),
        )
        try:
            app._device_panel = {
                'sim-1': DevicePanelEntry(
                    device_uid='sim-1',
                    strip_id='main',
                    length=60,
                    status='offline',
                    disconnected_at_ns=0,
                )
            }
            text = ''.join(fragment[1] for fragment in app._render_panel_rows())
            assert 'sim-1' in text
            assert 'main' in text
            assert '60' in text
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
            str(tmp_path / 'config.json'),
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

    def test_newdevice_dialog_sends_add_device_command(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            str(tmp_path / 'config.json'),
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

        assert app._newdevice_dialog is None
        assert client.commands == [{
            'cmd': 'add_device',
            'device_uid': 'sim-1',
            'device_type': 'sim',
            'strip_id': 'main',
            'length': 60,
            'id': 1,
        }]

    def test_newdevice_dialog_validation_error_stays_open(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            str(tmp_path / 'config.json'),
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
            str(tmp_path / 'config.json'),
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

    def test_rmdevice_sends_remove_device_command(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            str(tmp_path / 'config.json'),
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

    def test_devices_connected_uses_live_panel(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            str(tmp_path / 'config.json'),
            log_file=str(tmp_path / 'tui.log'),
        )
        client = self._FakeClient()
        app._set_client(client)
        app._device_panel = {
            'sim-2': DevicePanelEntry(
                device_uid='sim-2',
                strip_id='aux',
                length=30,
                status='configured',
            ),
            'sim-1': DevicePanelEntry(
                device_uid='sim-1',
                strip_id='main',
                length=60,
                status='connected',
            ),
        }

        try:
            app._do_devices()
        finally:
            if app._log_fp is not None:
                app._log_fp.close()

        assert client.commands == []
        assert app._log_lines[0].endswith('devices:')
        assert app._log_lines[1].endswith('  sim-2: strip "aux", 30 LEDs [configured]')
        assert app._log_lines[2].endswith('  sim-1: strip "main", 60 LEDs [connected]')

    def test_newdevice_requires_connected_controller(self, tmp_path):
        app = TuiApp(
            '/tmp/elemctl.sock',
            str(tmp_path / 'config.json'),
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
            str(tmp_path / 'config.json'),
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


class TestTuiMain:
    def test_missing_config_path_is_allowed(self, monkeypatch, tmp_path):
        called = {}

        class FakeApp:
            def __init__(self, socket_path, config_path, animations_dir, log_file):
                called["socket_path"] = socket_path
                called["config_path"] = config_path
                called["animations_dir"] = animations_dir
                called["log_file"] = log_file

            def run(self):
                called["ran"] = True

        monkeypatch.setattr('elemctl.tui.TuiApp', FakeApp)
        monkeypatch.setattr(
            'sys.argv',
            [
                'elemctl.tui',
                '--config', str(tmp_path / 'missing.json'),
                '--log-dir', str(tmp_path / 'logs'),
            ],
        )

        from elemctl.tui import main as tui_main
        tui_main()

        assert called["config_path"] == str(tmp_path / 'missing.json')
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


# ---------------------------------------------------------------------------
# extract_metadata tests
# ---------------------------------------------------------------------------

class TestExtractMetadata:
    def test_valid_float(self, tmp_path):
        f = tmp_path / "prog.py"
        f.write_text("BEAT = 0.5\nDURATION = 8.0\n")
        beat, dur = extract_metadata(str(f))
        assert beat == 0.5
        assert dur == 8.0

    def test_valid_int(self, tmp_path):
        f = tmp_path / "prog.py"
        f.write_text("BEAT = 1\nDURATION = 8\n")
        beat, dur = extract_metadata(str(f))
        assert beat == 1
        assert dur == 8

    def test_missing_beat(self, tmp_path):
        f = tmp_path / "prog.py"
        f.write_text("DURATION = 8.0\n")
        with pytest.raises(ValueError, match="missing BEAT"):
            extract_metadata(str(f))

    def test_missing_duration(self, tmp_path):
        f = tmp_path / "prog.py"
        f.write_text("BEAT = 0.5\n")
        with pytest.raises(ValueError, match="missing DURATION"):
            extract_metadata(str(f))

    def test_string_value(self, tmp_path):
        f = tmp_path / "prog.py"
        f.write_text('BEAT = "fast"\nDURATION = 8.0\n')
        with pytest.raises(ValueError, match="numeric literal"):
            extract_metadata(str(f))

    def test_expression_rejected(self, tmp_path):
        f = tmp_path / "prog.py"
        f.write_text("BEAT = 60 / 120\nDURATION = 8.0\n")
        with pytest.raises(ValueError, match="numeric literal"):
            extract_metadata(str(f))

    def test_syntax_error(self, tmp_path):
        f = tmp_path / "prog.py"
        f.write_text("BEAT = (\n")
        with pytest.raises(ValueError, match="syntax error"):
            extract_metadata(str(f))

    def test_non_positive_value(self, tmp_path):
        f = tmp_path / "prog.py"
        f.write_text("BEAT = 0\nDURATION = 8.0\n")
        with pytest.raises(ValueError, match="must be positive"):
            extract_metadata(str(f))

    def test_negative_value(self, tmp_path):
        """BEAT = -1 is ast.UnaryOp, not ast.Constant."""
        f = tmp_path / "prog.py"
        f.write_text("BEAT = -1\nDURATION = 8.0\n")
        with pytest.raises(ValueError, match="numeric literal"):
            extract_metadata(str(f))

    def test_nonexistent_file(self):
        with pytest.raises(ValueError, match="cannot read"):
            extract_metadata("/no/such/file.py")

    def test_extra_code_ignored(self, tmp_path):
        f = tmp_path / "prog.py"
        f.write_text("BEAT = 0.5\nDURATION = 4.0\nx = 42\nimport os\n")
        beat, dur = extract_metadata(str(f))
        assert beat == 0.5
        assert dur == 4.0

    def test_duplicate_beat_rejected(self, tmp_path):
        f = tmp_path / "prog.py"
        f.write_text("BEAT = 0.5\nBEAT = 1.0\nDURATION = 2.0\n")
        with pytest.raises(ValueError, match="duplicate BEAT"):
            extract_metadata(str(f))

    def test_duplicate_duration_rejected(self, tmp_path):
        f = tmp_path / "prog.py"
        f.write_text("BEAT = 0.5\nDURATION = 2.0\nDURATION = 4.0\n")
        with pytest.raises(ValueError, match="duplicate DURATION"):
            extract_metadata(str(f))


# ---------------------------------------------------------------------------
# scan_animations tests
# ---------------------------------------------------------------------------

class TestScanAnimations:
    def test_empty_directory(self, tmp_path):
        assert scan_animations(str(tmp_path)) == []

    def test_nonexistent_directory(self):
        assert scan_animations("/no/such/dir") == []

    def test_valid_files(self, tmp_path):
        (tmp_path / "alpha.py").write_text("BEAT = 1\nDURATION = 4\n")
        (tmp_path / "beta.py").write_text("BEAT = 0.5\nDURATION = 8.0\n")
        entries = scan_animations(str(tmp_path))
        assert len(entries) == 2
        assert entries[0].name == "alpha"
        assert entries[0].beat == 1
        assert entries[0].duration == 4
        assert entries[0].error is None
        assert entries[1].name == "beta"
        assert entries[1].beat == 0.5
        assert entries[1].duration == 8.0

    def test_invalid_file(self, tmp_path):
        (tmp_path / "broken.py").write_text("x = 1\n")
        entries = scan_animations(str(tmp_path))
        assert len(entries) == 1
        assert entries[0].name == "broken"
        assert entries[0].beat is None
        assert entries[0].duration is None
        assert entries[0].error is not None

    def test_non_py_excluded(self, tmp_path):
        (tmp_path / "notes.txt").write_text("hello\n")
        (tmp_path / "prog.py").write_text("BEAT = 1\nDURATION = 2\n")
        entries = scan_animations(str(tmp_path))
        assert len(entries) == 1
        assert entries[0].name == "prog"

    def test_sorted_alphabetically(self, tmp_path):
        (tmp_path / "zebra.py").write_text("BEAT = 1\nDURATION = 2\n")
        (tmp_path / "apple.py").write_text("BEAT = 1\nDURATION = 2\n")
        (tmp_path / "mango.py").write_text("BEAT = 1\nDURATION = 2\n")
        entries = scan_animations(str(tmp_path))
        names = [e.name for e in entries]
        assert names == ["apple", "mango", "zebra"]
