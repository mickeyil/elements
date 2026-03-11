"""Minimal TUI shell for elemctl — connects to the controller service over UDS.

Usage: python -m elemctl.tui [--socket PATH]
"""

from __future__ import annotations

import argparse
import json
import queue
import select
import threading

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.layout import Layout

from .config import DEFAULT_SOCKET_PATH
from .uds_client import UdsClient
from .uds_wire import KIND_FRAME, KIND_JSON, parse_json_payload


# ------------------------------------------------------------------
# Command parsing
# ------------------------------------------------------------------

_QUIT_SENTINEL = object()

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

    if name == 'quit':
        if len(parts) > 1:
            return None, "/quit does not take arguments"
        return _QUIT_SENTINEL, None

    if name not in _COMMANDS:
        return None, f"unknown command: /{name}"

    if len(parts) > 1:
        return None, f"/{name} does not take arguments"

    return {'cmd': name, 'id': next_id}, None


# ------------------------------------------------------------------
# Event formatting
# ------------------------------------------------------------------

def format_event(kind: int, payload: bytes) -> str | None:
    """Format a UDS message as a single-line log entry. Returns None for frames."""
    if kind == KIND_FRAME:
        return None  # silently counted, not displayed

    if kind != KIND_JSON:
        return f"[unknown] kind={kind} len={len(payload)}"

    try:
        msg = parse_json_payload(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return f"[unknown] bad json len={len(payload)}"

    msg_type = msg.get('type')

    if msg_type == 'reply':
        rid = msg.get('id')
        if msg.get('ok'):
            result = msg.get('result', {})
            return f"[reply:{rid}] ok {json.dumps(result, separators=(',', ':'))}"
        else:
            return f"[reply:{rid}] ERROR: {msg.get('error', '?')}"

    if msg_type == 'event':
        return _format_controller_event(msg)

    return f"[unknown] {json.dumps(msg, separators=(',', ':'))}"


def _format_controller_event(msg: dict) -> str:
    event = msg.get('event')

    if event == 'snapshot':
        online = msg.get('online_count', '?')
        expected = msg.get('expected_count', '?')
        session = msg.get('session')
        if session:
            state = session.get('playback_state', '?')
            sid = session.get('session_id')
            return f"[snapshot] state={state} online={online}/{expected} session={sid}"
        return f"[snapshot] state=idle online={online}/{expected} session=None"

    if event == 'state':
        state = msg.get('state', '?')
        epoch = msg.get('epoch', '?')
        sid = msg.get('session_id', '?')
        return f"[state] {state} epoch={epoch} session={sid}"

    if event == 'device_status':
        uid = msg.get('device_uid', '?')
        strip = msg.get('strip', '?')
        connected = msg.get('connected')
        status = 'connected' if connected else 'disconnected'
        return f"[device] {uid} ({strip}) {status}"

    if event == 'session_start':
        sid = msg.get('session_id', '?')
        epoch = msg.get('epoch', '?')
        duration = msg.get('duration', '?')
        strips = ', '.join(s.get('name', '?') for s in msg.get('strips', []))
        return f"[session] id={sid} epoch={epoch} duration={duration}s strips=[{strips}]"

    if event == 'loop':
        epoch = msg.get('epoch', '?')
        sid = msg.get('session_id', '?')
        return f"[loop] epoch={epoch} session={sid}"

    if event == 'error':
        return f"[error] {msg.get('message', '?')}"

    return f"[unknown] {json.dumps(msg, separators=(',', ':'))}"


# ------------------------------------------------------------------
# TUI application
# ------------------------------------------------------------------

class TuiApp:
    """Full-screen TUI shell for elemctl."""

    def __init__(self, socket_path: str):
        self._socket_path = socket_path
        self._client: UdsClient | None = None
        self._shutdown = threading.Event()
        self._next_id = 1
        self._log_lines: list[str] = []
        self._pending_logs: queue.Queue[str] = queue.Queue()
        self._reader_thread: threading.Thread | None = None

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

        self._app = Application(
            layout=Layout(
                HSplit([
                    Window(content=BufferControl(buffer=self._log_buffer), wrap_lines=True),
                    Window(height=1, content=FormattedTextControl([('class:separator', '─' * 200)])),
                    Window(height=1, content=input_control),
                ]),
                focused_element=input_control,
            ),
            key_bindings=kb,
            full_screen=True,
            before_render=self._drain_pending_logs,
        )

    def run(self) -> None:
        """Connect and run the TUI. Blocks until exit."""
        try:
            self._client = UdsClient(self._socket_path)
        except (OSError, ConnectionError) as e:
            self._append_log(f"[tui] connection failed: {e}")
            self._append_log("[tui] start the service with: python -m elemctl.serve")
            self._append_log("[tui] press Ctrl-C to exit")
            self._app.run()
            return

        self._append_log(f"[tui] connected to {self._socket_path}")

        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

        try:
            self._app.run()
        finally:
            self._shutdown.set()
            if self._client is not None:
                self._client.close()

    # ------------------------------------------------------------------
    # Input handling
    # ------------------------------------------------------------------

    def _on_input(self, buff: Buffer) -> None:
        text = buff.text
        cmd, error = parse_command(text, self._next_id)

        if cmd is None:
            if error:
                self._append_log(f"[tui] {error}")
            return

        if cmd is _QUIT_SENTINEL:
            self._exit()
            return

        self._append_log(f"> {text}")
        self._next_id += 1

        if self._client is None:
            self._append_log("[tui] not connected")
            return

        try:
            self._client.send_cmd(cmd)
        except OSError as e:
            self._append_log(f"[tui] send failed: {e}")

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
                self._enqueue_log("[tui] disconnected")
                break

            for kind, payload in messages:
                if kind == KIND_FRAME:
                    continue
                line = format_event(kind, payload)
                if line is not None:
                    self._enqueue_log(line)

    # ------------------------------------------------------------------
    # Log pane
    # ------------------------------------------------------------------

    def _enqueue_log(self, line: str) -> None:
        """Thread-safe: push a log line for the UI thread to drain."""
        self._pending_logs.put(line)
        self._app.invalidate()

    def _append_log(self, line: str) -> None:
        """UI-thread only: directly append a log line and update the buffer."""
        self._log_lines.append(line)
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
    args = parser.parse_args()
    TuiApp(args.socket).run()


if __name__ == '__main__':
    main()
