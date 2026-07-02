"""Hub tests against scripted peers on loopback ephemeral ports.

Real sockets, fake clock: data arrival is real I/O, but every
scheduling decision (pings, ACK timeouts, the REGISTER deadline) is
driven by advancing the injected clock.
"""

import logging
import socket
import struct
import time

import pytest

from elemctl import hub as hub_mod
from elemctl import wire
from elemctl.hub import DeviceConnected, DeviceDisconnected, DeviceHub

WANTED = {'sim-a', 'sim-b'}


class FakeClock:
    def __init__(self):
        self.now_us = 1_000_000

    def __call__(self):
        return self.now_us

    def advance_ms(self, ms):
        self.now_us += ms * 1000


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def hub(clock):
    h = DeviceHub(discovery_port=0, link_port=0, frame_port=0, sync_port=0,
                  log_port=0, clock_us=clock)
    yield h
    h.close()


def poll_until(hub, condition, *, wanted=WANTED, timeout_s=2.0):
    """Poll the hub until condition(events, frames) or time runs out.
    Returns (events, frames) accumulated across polls."""
    events, frames = [], []
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = hub.poll(wanted)
        events.extend(result.events)
        frames.extend(result.frames)
        if condition(events, frames):
            return events, frames
    return events, frames


def register_payload(uid, boot_token=1, version=wire.PROTOCOL_VERSION):
    return uid.encode().ljust(16, b'\x00') + struct.pack('<IB', boot_token, version)


class ScriptedDevice:
    """A bare socket playing the device side of the link protocol."""

    def __init__(self, link_port):
        self.sock = socket.create_connection(('127.0.0.1', link_port), timeout=2.0)
        self.sock.settimeout(2.0)
        self.reader = wire.LinkReader()
        self.messages = []

    def register(self, uid='sim-a', boot_token=1, version=wire.PROTOCOL_VERSION):
        self.sock.sendall(wire.encode_message(
            wire.CMD_REGISTER, register_payload(uid, boot_token, version)))

    def ack(self, status=wire.ACK_OK, payload=b''):
        self.sock.sendall(wire.encode_message(wire.CMD_ACK, bytes([status]) + payload))

    def recv_messages(self, count=1, timeout_s=2.0):
        """Read until count messages are buffered; returns them in order."""
        deadline = time.monotonic() + timeout_s
        while len(self.messages) < count:
            self.sock.settimeout(max(0.01, deadline - time.monotonic()))
            data = self.sock.recv(4096)
            if not data:
                raise AssertionError('peer closed while awaiting messages')
            self.reader.feed(data)
            self.messages.extend(self.reader.messages())
        return self.messages[:count]

    def assert_closed(self, timeout_s=2.0):
        self.sock.settimeout(timeout_s)
        assert self.sock.recv(1) == b''

    def close(self):
        self.sock.close()


def connect_device(hub, uid='sim-a', boot_token=1):
    dev = ScriptedDevice(hub.link_port)
    dev.register(uid, boot_token)
    events, _ = poll_until(hub, lambda e, f: any(
        isinstance(ev, DeviceConnected) and ev.uid == uid for ev in e))
    assert hub.is_connected(uid), f'events: {events}'
    return dev


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_register_accepted(hub):
    dev = connect_device(hub, 'sim-a', boot_token=7)
    assert hub.connected_uids() == {'sim-a'}
    dev.close()


def test_first_connect_is_not_a_reboot(hub):
    dev = ScriptedDevice(hub.link_port)
    dev.register('sim-a', boot_token=7)
    events, _ = poll_until(hub, lambda e, f: bool(e))
    assert events == [DeviceConnected(uid='sim-a', boot_token=7, rebooted=False)]
    dev.close()


def test_unwanted_uid_silently_closed(hub):
    dev = ScriptedDevice(hub.link_port)
    dev.register('sim-zz')
    events, _ = poll_until(hub, lambda e, f: False, timeout_s=0.2)
    assert events == []
    assert not hub.is_connected('sim-zz')
    dev.assert_closed()


def test_wrong_protocol_version_closed(hub):
    dev = ScriptedDevice(hub.link_port)
    dev.register('sim-a', version=2)
    poll_until(hub, lambda e, f: False, timeout_s=0.2)
    assert not hub.is_connected('sim-a')
    dev.assert_closed()


def test_first_message_must_be_register(hub):
    dev = ScriptedDevice(hub.link_port)
    dev.ack()
    poll_until(hub, lambda e, f: False, timeout_s=0.2)
    dev.assert_closed()


def test_unregistered_connection_dropped_after_deadline(hub, clock):
    dev = ScriptedDevice(hub.link_port)
    poll_until(hub, lambda e, f: False, timeout_s=0.1)   # let the accept land
    clock.advance_ms(hub_mod.REGISTER_DEADLINE_S * 1000 + 1)
    poll_until(hub, lambda e, f: False, timeout_s=0.1)
    dev.assert_closed()


def test_duplicate_uid_new_connection_wins(hub):
    dev1 = connect_device(hub, 'sim-a', boot_token=5)
    dev2 = ScriptedDevice(hub.link_port)
    dev2.register('sim-a', boot_token=5)
    events, _ = poll_until(hub, lambda e, f: any(
        isinstance(ev, DeviceDisconnected) for ev in e))
    assert DeviceDisconnected(uid='sim-a', reason='replaced by new connection') in events
    assert DeviceConnected(uid='sim-a', boot_token=5, rebooted=False) in events
    assert hub.is_connected('sim-a')
    dev1.assert_closed()
    dev2.close()


def test_reconnect_with_new_boot_token_is_a_reboot(hub):
    dev1 = connect_device(hub, 'sim-a', boot_token=5)
    dev1.close()
    poll_until(hub, lambda e, f: any(isinstance(ev, DeviceDisconnected) for ev in e))

    dev2 = ScriptedDevice(hub.link_port)
    dev2.register('sim-a', boot_token=6)
    events, _ = poll_until(hub, lambda e, f: any(
        isinstance(ev, DeviceConnected) for ev in e))
    assert DeviceConnected(uid='sim-a', boot_token=6, rebooted=True) in events
    dev2.close()


# ---------------------------------------------------------------------------
# Commands and ACKs
# ---------------------------------------------------------------------------

def test_command_reaches_device_and_ack_reaches_callback(hub):
    dev = connect_device(hub)
    acks = []
    assert hub.send('sim-a', wire.encode_query_device_status(), acks.append)
    opcode, payload = dev.recv_messages(1)[0]
    assert opcode == wire.CMD_QUERY_DEVICE_STATUS

    dev.ack(wire.ACK_OK, struct.pack('<BBH', 2, 0, 0))
    poll_until(hub, lambda e, f: bool(acks))
    assert acks[0].status == wire.ACK_OK
    report = wire.parse_device_status(acks[0].payload)
    assert wire.DEVICE_MODE_NAMES[report.mode] == 'detached_blank'
    dev.close()


def test_acks_match_commands_in_fifo_order(hub):
    dev = connect_device(hub)
    got = []
    hub.send('sim-a', wire.encode_pause(), lambda ack: got.append(('pause', ack.status)))
    hub.send('sim-a', wire.encode_stop(), lambda ack: got.append(('stop', ack.status)))
    dev.recv_messages(2)
    dev.ack(wire.ACK_OK)
    dev.ack(wire.ACK_WRONG_STATE)
    poll_until(hub, lambda e, f: len(got) == 2)
    assert got == [('pause', wire.ACK_OK), ('stop', wire.ACK_WRONG_STATE)]
    dev.close()


def test_send_to_unknown_uid_returns_false(hub):
    assert hub.send('sim-a', wire.encode_pause()) is False


def test_ack_callback_exception_does_not_kill_the_link(hub):
    dev = connect_device(hub)

    def explode(ack):
        raise RuntimeError('session bug')

    hub.send('sim-a', wire.encode_pause(), explode)
    dev.recv_messages(1)
    dev.ack()
    poll_until(hub, lambda e, f: False, timeout_s=0.2)
    assert hub.is_connected('sim-a')
    dev.close()


# ---------------------------------------------------------------------------
# Liveness
# ---------------------------------------------------------------------------

def test_ping_sent_after_idle_interval(hub, clock):
    dev = connect_device(hub)
    clock.advance_ms(wire.PING_INTERVAL_MS)
    poll_until(hub, lambda e, f: False, timeout_s=0.1)
    opcode, payload = dev.recv_messages(1)[0]
    assert opcode == wire.CMD_PING
    assert payload == b''
    dev.close()


def test_unanswered_ack_drops_the_link(hub, clock):
    dev = connect_device(hub)
    hub.send('sim-a', wire.encode_pause())
    dev.recv_messages(1)            # delivered, never ACKed
    clock.advance_ms(hub_mod.ACK_TIMEOUT_US // 1000 + 1)
    events, _ = poll_until(hub, lambda e, f: any(
        isinstance(ev, DeviceDisconnected) for ev in e))
    assert DeviceDisconnected(uid='sim-a', reason='ack timeout') in events
    assert not hub.is_connected('sim-a')
    dev.close()


def test_device_closing_emits_disconnect(hub):
    dev = connect_device(hub)
    dev.close()
    events, _ = poll_until(hub, lambda e, f: bool(e))
    assert events == [DeviceDisconnected(uid='sim-a', reason='closed by device')]


# ---------------------------------------------------------------------------
# Protocol violations
# ---------------------------------------------------------------------------

def test_corrupt_stream_drops_the_link(hub):
    dev = connect_device(hub)
    dev.sock.sendall(b'\x00\x00\x00\x00')   # impossible length
    events, _ = poll_until(hub, lambda e, f: bool(e))
    assert DeviceDisconnected(uid='sim-a', reason='protocol error') in events


def test_non_ack_opcode_from_device_drops_the_link(hub):
    dev = connect_device(hub)
    dev.sock.sendall(wire.encode_message(wire.CMD_LOAD, b'x'))
    events, _ = poll_until(hub, lambda e, f: bool(e))
    assert DeviceDisconnected(uid='sim-a', reason='protocol error') in events


def test_unsolicited_ack_drops_the_link(hub):
    dev = connect_device(hub)
    dev.ack()
    events, _ = poll_until(hub, lambda e, f: bool(e))
    assert DeviceDisconnected(uid='sim-a', reason='protocol error') in events


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_packet(uid):
    return b'\xcc\xd1\x01' + uid.encode().ljust(16, b'\x00')


def test_discover_answered_with_offer(hub):
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.settimeout(2.0)
    client.sendto(discover_packet('sim-a'), ('127.0.0.1', hub.discovery_port))

    deadline = time.monotonic() + 2.0
    offer = None
    while offer is None and time.monotonic() < deadline:
        hub.poll(WANTED)
        try:
            client.settimeout(0.05)
            offer, _ = client.recvfrom(64)
        except socket.timeout:
            continue

    assert offer is not None
    magic, pkt_type = struct.unpack_from('<HB', offer, 0)
    assert magic == wire.DISCOVERY_MAGIC and pkt_type == wire.PKT_OFFER
    assert socket.inet_ntoa(offer[3:7]) == '127.0.0.1'   # routed toward loopback
    assert struct.unpack_from('<H', offer, 7)[0] == hub.link_port
    client.close()


def test_unwanted_discover_gets_no_offer(hub):
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.sendto(discover_packet('sim-zz'), ('127.0.0.1', hub.discovery_port))
    poll_until(hub, lambda e, f: False, timeout_s=0.2)
    client.settimeout(0.1)
    with pytest.raises(socket.timeout):
        client.recvfrom(64)
    client.close()


def poll_until_discovered(hub, uid, timeout_s=2.0):
    deadline = time.monotonic() + timeout_s
    while uid not in hub.discovered_uids() and time.monotonic() < deadline:
        hub.poll(WANTED)
    return hub.discovered_uids()


def test_valid_unwanted_discover_is_recorded_but_not_offered(hub):
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.sendto(discover_packet('sim-new'), ('127.0.0.1', hub.discovery_port))
    assert 'sim-new' in poll_until_discovered(hub, 'sim-new')
    client.settimeout(0.1)
    with pytest.raises(socket.timeout):
        client.recvfrom(64)              # recorded, but never offered
    client.close()


def test_invalid_discover_uid_is_not_recorded(hub):
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.sendto(discover_packet('bad-uid'), ('127.0.0.1', hub.discovery_port))
    poll_until(hub, lambda e, f: False, timeout_s=0.2)
    assert hub.discovered_uids() == set()
    client.close()


def test_discovered_uid_ages_out(hub, clock):
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.sendto(discover_packet('sim-new'), ('127.0.0.1', hub.discovery_port))
    assert 'sim-new' in poll_until_discovered(hub, 'sim-new')
    clock.advance_ms(hub_mod.DISCOVERED_TTL_US // 1000 + 1)
    assert hub.discovered_uids() == set()
    client.close()


# ---------------------------------------------------------------------------
# Clock sync
# ---------------------------------------------------------------------------

def sync_ping_packet(uid, boot_token, seq, t1_us):
    return (b'\x01' + uid.encode().ljust(16, b'\x00')
            + struct.pack('<IIq', boot_token, seq, t1_us))


def test_sync_ping_answered_for_registered_device(hub, clock):
    dev = connect_device(hub, 'sim-a', boot_token=42)
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.settimeout(2.0)
    client.sendto(sync_ping_packet('sim-a', 42, seq=9, t1_us=12345),
                  ('127.0.0.1', hub.sync_port))

    deadline = time.monotonic() + 2.0
    pong = None
    while pong is None and time.monotonic() < deadline:
        hub.poll(WANTED)
        try:
            client.settimeout(0.05)
            pong, _ = client.recvfrom(64)
        except socket.timeout:
            continue

    assert pong is not None and len(pong) == wire.SYNC_PONG_WIRE_SIZE
    pkt_type, token, seq, t1, t2, t3 = struct.unpack('<BIIqqq', pong)
    assert pkt_type == wire.SYNC_PKT_PONG
    assert token == hub.boot_token != 0
    assert (seq, t1) == (9, 12345)
    assert t2 == t3 == clock.now_us   # the fake clock stamps both
    client.close()
    dev.close()


@pytest.mark.parametrize('uid,boot_token', [
    ('sim-a', 41),    # registered uid, wrong boot_token
    ('sim-b', 42),    # never-registered uid
])
def test_sync_ping_discarded_without_admission(hub, uid, boot_token):
    dev = connect_device(hub, 'sim-a', boot_token=42)
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.sendto(sync_ping_packet(uid, boot_token, seq=1, t1_us=1),
                  ('127.0.0.1', hub.sync_port))
    poll_until(hub, lambda e, f: False, timeout_s=0.2)
    client.settimeout(0.1)
    with pytest.raises(socket.timeout):
        client.recvfrom(64)
    client.close()
    dev.close()


# ---------------------------------------------------------------------------
# Frame previews
# ---------------------------------------------------------------------------

def frame_packet(uid, frame_index=0, t_program=0.0, pixels=1):
    return (uid.encode().ljust(16, b'\x00')
            + struct.pack('<If', frame_index, t_program)
            + b'\x10\x20\x30' * pixels)


def test_frames_from_registered_sim_are_delivered(hub):
    dev = connect_device(hub, 'sim-a')
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.sendto(frame_packet('sim-a', frame_index=3, pixels=2),
                  ('127.0.0.1', hub.frame_port))
    _, frames = poll_until(hub, lambda e, f: bool(f))
    assert len(frames) == 1
    assert frames[0].uid == 'sim-a'
    assert frames[0].frame_index == 3
    assert frames[0].rgb == b'\x10\x20\x30' * 2
    client.close()
    dev.close()


def test_frames_from_unknown_uid_are_dropped(hub):
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.sendto(frame_packet('sim-a'), ('127.0.0.1', hub.frame_port))
    _, frames = poll_until(hub, lambda e, f: False, timeout_s=0.2)
    assert frames == []
    client.close()


# ---------------------------------------------------------------------------
# Device logs
# ---------------------------------------------------------------------------

def log_packet(uid, boot_token=1, seq=1, uptime_ms=1000, level=b'I',
               text=b'hello from device'):
    return (struct.pack('<HB', wire.LOG_MAGIC, wire.LOG_VERSION)
            + uid.encode().ljust(16, b'\x00')
            + struct.pack('<III', boot_token, seq, uptime_ms)
            + level + text)


@pytest.fixture
def log_client():
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    yield client
    client.close()


def test_device_log_lands_in_controller_log(hub, log_client, caplog):
    # No link is established: a configured uid logs even when it
    # cannot register, which is the point of the channel.
    caplog.set_level(logging.INFO, logger='elemctl.hub')
    log_client.sendto(log_packet('sim-a', level=b'E', text=b'boom'),
                      ('127.0.0.1', hub.log_port))
    poll_until(hub, lambda e, f: 'boom' in caplog.text)

    records = [r for r in caplog.records if 'boom' in r.getMessage()]
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    assert records[0].getMessage() == 'sim-a: boom'


def test_device_log_from_unwanted_uid_is_dropped(hub, log_client, caplog):
    caplog.set_level(logging.INFO, logger='elemctl.hub')
    log_client.sendto(log_packet('sim-zz', text=b'noise'),
                      ('127.0.0.1', hub.log_port))
    poll_until(hub, lambda e, f: False, timeout_s=0.2)
    assert 'noise' not in caplog.text


def test_device_log_gap_is_reported(hub, log_client, caplog):
    caplog.set_level(logging.INFO, logger='elemctl.hub')
    log_client.sendto(log_packet('sim-a', seq=1, text=b'one'),
                      ('127.0.0.1', hub.log_port))
    poll_until(hub, lambda e, f: 'one' in caplog.text)

    log_client.sendto(log_packet('sim-a', seq=5, text=b'five'),
                      ('127.0.0.1', hub.log_port))
    poll_until(hub, lambda e, f: 'five' in caplog.text)
    assert 'lost 3 log records' in caplog.text


def test_device_log_reboot_resets_gap_tracking(hub, log_client, caplog):
    caplog.set_level(logging.INFO, logger='elemctl.hub')
    log_client.sendto(log_packet('sim-a', boot_token=1, seq=1, text=b'old boot'),
                      ('127.0.0.1', hub.log_port))
    poll_until(hub, lambda e, f: 'old boot' in caplog.text)

    log_client.sendto(log_packet('sim-a', boot_token=2, seq=1, text=b'new boot'),
                      ('127.0.0.1', hub.log_port))
    poll_until(hub, lambda e, f: 'new boot' in caplog.text)
    assert 'lost' not in caplog.text


def test_device_log_first_seq_above_one_reports_the_lost_head(
        hub, log_client, caplog):
    # The device's ring wrapped before anything shipped: the first
    # record the controller ever sees for this boot is seq 5.
    caplog.set_level(logging.INFO, logger='elemctl.hub')
    log_client.sendto(log_packet('sim-a', seq=5, text=b'late start'),
                      ('127.0.0.1', hub.log_port))
    poll_until(hub, lambda e, f: 'late start' in caplog.text)
    assert 'lost 4 log records' in caplog.text


def test_device_log_stale_boot_straggler_does_not_derail_tracking(
        hub, log_client, caplog):
    caplog.set_level(logging.INFO, logger='elemctl.hub')

    def send_and_wait(boot_token, seq, text):
        log_client.sendto(
            log_packet('sim-a', boot_token=boot_token, seq=seq, text=text),
            ('127.0.0.1', hub.log_port))
        poll_until(hub, lambda e, f: text.decode() in caplog.text)

    send_and_wait(1, 1, b'boot one')
    send_and_wait(2, 1, b'boot two')
    send_and_wait(1, 2, b'straggler from boot one')   # late, reordered
    assert 'lost' not in caplog.text                  # no spurious gap

    send_and_wait(2, 5, b'boot two continues')
    assert 'lost 3 log records' in caplog.text        # real gap still seen
