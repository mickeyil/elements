"""Tests for TUI non-UI logic: format_event, parse_command, metadata, scan."""

import json
import socket

import pytest

from elemctl.tui import (
    format_event, parse_command, extract_metadata, scan_animations,
    AnimationEntry, _QUIT_SENTINEL, _RESCAN_SENTINEL,
)
from elemctl.uds_wire import KIND_JSON, KIND_FRAME, UdsReader, encode_json
from elemctl.uds_client import UdsClient


# ---------------------------------------------------------------------------
# format_event tests
# ---------------------------------------------------------------------------

def _json_payload(obj: dict) -> bytes:
    return json.dumps(obj, separators=(',', ':')).encode('utf-8')


class TestFormatEvent:
    def test_snapshot_idle(self):
        msg = {
            'type': 'event', 'event': 'snapshot',
            'online_count': 2, 'expected_count': 3,
            'session': None,
        }
        result = format_event(KIND_JSON, _json_payload(msg))
        assert result == '[snapshot] state=idle online=2/3 session=None'

    def test_snapshot_with_session(self):
        msg = {
            'type': 'event', 'event': 'snapshot',
            'online_count': 1, 'expected_count': 2,
            'session': {
                'playback_state': 'playing',
                'session_id': 5,
            },
        }
        result = format_event(KIND_JSON, _json_payload(msg))
        assert result == '[snapshot] state=playing online=1/2 session=5'

    def test_state_event(self):
        msg = {
            'type': 'event', 'event': 'state',
            'state': 'playing', 'epoch': 1, 'session_id': 3,
        }
        result = format_event(KIND_JSON, _json_payload(msg))
        assert result == '[state] playing epoch=1 session=3'

    def test_device_connected(self):
        msg = {
            'type': 'event', 'event': 'device_status',
            'device_uid': 'sim-1', 'strip': 'strip_a', 'connected': True,
        }
        result = format_event(KIND_JSON, _json_payload(msg))
        assert result == '[device] sim-1 (strip_a) connected'

    def test_device_disconnected(self):
        msg = {
            'type': 'event', 'event': 'device_status',
            'device_uid': 'sim-2', 'strip': 'strip_b', 'connected': False,
        }
        result = format_event(KIND_JSON, _json_payload(msg))
        assert result == '[device] sim-2 (strip_b) disconnected'

    def test_session_start(self):
        msg = {
            'type': 'event', 'event': 'session_start',
            'session_id': 1, 'epoch': 0, 'duration': 2.0,
            'strips': [{'name': 'strip_a'}, {'name': 'strip_b'}],
        }
        result = format_event(KIND_JSON, _json_payload(msg))
        assert result == "[session] id=1 epoch=0 duration=2.0s strips=[strip_a, strip_b]"

    def test_loop(self):
        msg = {
            'type': 'event', 'event': 'loop',
            'epoch': 3, 'session_id': 1,
        }
        result = format_event(KIND_JSON, _json_payload(msg))
        assert result == '[loop] epoch=3 session=1'

    def test_error(self):
        msg = {
            'type': 'event', 'event': 'error',
            'message': 'something broke',
        }
        result = format_event(KIND_JSON, _json_payload(msg))
        assert result == '[error] something broke'

    def test_reply_ok(self):
        msg = {'type': 'reply', 'id': 7, 'ok': True, 'result': {'x': 1}}
        result = format_event(KIND_JSON, _json_payload(msg))
        assert result == '[reply:7] ok {"x":1}'

    def test_reply_error(self):
        msg = {'type': 'reply', 'id': 3, 'ok': False, 'error': 'bad cmd'}
        result = format_event(KIND_JSON, _json_payload(msg))
        assert result == '[reply:3] ERROR: bad cmd'

    def test_frame_returns_none(self):
        assert format_event(KIND_FRAME, b'\x00' * 20) is None

    def test_unknown_event(self):
        msg = {'type': 'event', 'event': 'future_thing', 'data': 42}
        result = format_event(KIND_JSON, _json_payload(msg))
        assert '[unknown]' in result

    def test_unknown_kind(self):
        result = format_event(0xFF, b'hello')
        assert result == '[unknown] kind=255 len=5'

    def test_unknown_msg_type(self):
        msg = {'type': 'something_else', 'x': 1}
        result = format_event(KIND_JSON, _json_payload(msg))
        assert '[unknown]' in result

    def test_bad_json(self):
        result = format_event(KIND_JSON, b'\xff\xfe')
        assert '[unknown]' in result
        assert 'bad json' in result


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
