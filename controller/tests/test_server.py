"""Server frame-subscription tests over real sockets.

These drive the server's connection bookkeeping directly (no accept loop, no
handshake timing) using socketpairs as client connections, so the
subscribe/target/disable behavior is exercised against real socket writes.
"""

import socket

from elemctl.controller_protocol import (
    KIND_FRAME,
    ROLE_OBSERVER,
    ProtocolReader,
    encode_frame,
)
from elemctl.server import ControllerServer, _ClientConn


class FakeService:
    """Only the surface the server touches in these paths."""

    def __init__(self):
        self.preview_enabled = False

    def set_preview_enabled(self, enabled):
        self.preview_enabled = enabled


FRAME = encode_frame(1, 0.02, [b'\x00\x00\x03'])


def make_server():
    return ControllerServer(FakeService(), '/unused.sock')


def add_conn(server, role=ROLE_OBSERVER):
    """A connected, hello-completed client; returns its peer socket to read."""
    near, peer = socket.socketpair()
    near.setblocking(False)
    peer.settimeout(0.2)
    conn = _ClientConn(sock=near, reader=ProtocolReader(), desc=None,
                       role=role, hello_ok=True)
    server._clients[near.fileno()] = conn
    return conn, peer


def kinds_on(peer):
    """Drain and decode the message kinds waiting on a peer socket."""
    reader = ProtocolReader()
    try:
        data = peer.recv(65536)
    except socket.timeout:
        return []
    reader.feed(data)
    return [kind for kind, _ in reader.messages()]


def test_subscribe_enables_and_targets_frames():
    server = make_server()
    conn, peer = add_conn(server)

    server._handle_subscription(conn, 1, 'subscribe_frames')
    assert server._service.preview_enabled is True
    assert server._frame_subscriber is conn

    server._send_frames([FRAME])
    assert KIND_FRAME in kinds_on(peer)          # reply (json) then the frame


def test_non_subscriber_gets_no_frames():
    server = make_server()
    sub, sub_peer = add_conn(server)
    other, other_peer = add_conn(server)

    server._handle_subscription(sub, 1, 'subscribe_frames')
    kinds_on(sub_peer)                            # drain the subscribe reply
    server._send_frames([FRAME])

    assert KIND_FRAME in kinds_on(sub_peer)
    assert KIND_FRAME not in kinds_on(other_peer)


def test_last_subscriber_wins_without_retoggle():
    server = make_server()
    a, a_peer = add_conn(server)
    b, b_peer = add_conn(server)

    server._handle_subscription(a, 1, 'subscribe_frames')
    server._handle_subscription(b, 2, 'subscribe_frames')   # B replaces A
    assert server._frame_subscriber is b
    assert server._service.preview_enabled is True          # never toggled off

    kinds_on(a_peer); kinds_on(b_peer)
    server._send_frames([FRAME])
    assert KIND_FRAME in kinds_on(b_peer)
    assert KIND_FRAME not in kinds_on(a_peer)


def test_stale_unsubscribe_ignored():
    server = make_server()
    a, _ = add_conn(server)
    b, _ = add_conn(server)
    server._handle_subscription(a, 1, 'subscribe_frames')
    server._handle_subscription(b, 2, 'subscribe_frames')   # current is B

    server._handle_subscription(a, 3, 'unsubscribe_frames')  # A is stale
    assert server._frame_subscriber is b
    assert server._service.preview_enabled is True


def test_unsubscribe_disables():
    server = make_server()
    conn, _ = add_conn(server)
    server._handle_subscription(conn, 1, 'subscribe_frames')
    server._handle_subscription(conn, 2, 'unsubscribe_frames')
    assert server._frame_subscriber is None
    assert server._service.preview_enabled is False


def test_subscriber_disconnect_disables():
    server = make_server()
    conn, _ = add_conn(server)
    server._handle_subscription(conn, 1, 'subscribe_frames')
    server._close_client(conn)
    assert server._frame_subscriber is None
    assert server._service.preview_enabled is False
