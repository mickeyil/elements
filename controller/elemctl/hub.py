"""The device hub: every socket the controller shares with devices.

Four listeners on the four well-known ports, one nonblocking poll
loop, everything keyed by UID:

  DiscoveryServer  UDP 6040  answers DISCOVER with OFFER
  LinkServer       TCP 6041  REGISTER validation, commands out, ACKs back
  FrameReceiver    UDP 6042  sim preview frames
  SyncServer       UDP 6043  stateless clock-sync PONGs

The hub owns connections and bytes, never meaning: it matches each
ACK to the callback that sent the command and reports device arrivals
and departures; what a command or status means belongs to the session
layer above. drafts/controller_v3.md ("The device hub") is the spec.

Construct a DeviceHub, then call poll(wanted_uids) once per tick; it
returns the events and preview frames that arrived. clock_us must be
the same monotonic microsecond clock the session layer stamps
program_start_us with; the sync server hands it to devices.
"""

import logging
import random
import socket
import time

from dataclasses import dataclass

from . import wire

log = logging.getLogger(__name__)

PING_INTERVAL_US = wire.PING_INTERVAL_MS * 1_000

# A connection that has not sent REGISTER after this long is not a
# device (devices register immediately after connect); reclaim the fd.
REGISTER_DEADLINE_S = 5

# Oldest unanswered command before the link counts as dead. Devices
# ACK within milliseconds, and idle pings keep commands flowing, so
# two ping intervals of silence means the device is gone.
ACK_TIMEOUT_US = 2 * PING_INTERVAL_US

_TCP_RECV_CHUNK = 4096
_UDP_RECV_SIZE = 2048


@dataclass(frozen=True)
class DeviceConnected:
    uid: str
    boot_token: int
    rebooted: bool   # boot_token changed since this uid was last seen


@dataclass(frozen=True)
class DeviceDisconnected:
    uid: str
    reason: str


@dataclass
class HubPoll:
    events: list     # DeviceConnected / DeviceDisconnected, in order
    frames: list     # wire.FramePreview from registered sims


def _advertised_ip_toward(src_ip):
    """Pick which of our local IPs a device at src_ip should connect back to.

    On a machine with several network interfaces, the right answer depends
    on where the device is. Connecting a UDP socket triggers the kernel's
    route lookup without sending any packet; getsockname() then reveals the
    local IP it picked. Returns None when there's no route to src_ip.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((src_ip, 1))     # route lookup only; UDP sends nothing
        return probe.getsockname()[0]  # the source address the route chose
    except OSError:
        return None                    # no route to src_ip
    finally:
        probe.close()


def _udp_listener(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('', port))
    sock.setblocking(False)
    return sock


class DiscoveryServer:
    """Answers DISCOVER broadcasts from wanted devices with a unicast
    OFFER, every time; a device treats a quiet controller as gone."""

    def __init__(self, port, link_port):
        self._sock = _udp_listener(port)
        self._link_port = link_port

    @property
    def port(self):
        return self._sock.getsockname()[1]

    def poll(self, wanted_uids):
        while True:
            try:
                datagram, src = self._sock.recvfrom(_UDP_RECV_SIZE)
            except BlockingIOError:
                return
            except OSError as e:
                log.warning('discovery: recv failed: %s', e)
                return
            uid = wire.parse_discover(datagram)
            if uid is None or uid not in wanted_uids:
                continue
            advertised = _advertised_ip_toward(src[0])
            if advertised is None:
                log.debug('discovery: no route toward %s; skipping OFFER', src[0])
                continue
            try:
                self._sock.sendto(wire.encode_offer(advertised, self._link_port), src)
            except OSError as e:
                log.debug('discovery: OFFER to %s failed: %s', src, e)

    def close(self):
        self._sock.close()


class SyncServer:
    """Stateless PONG responder (drafts/synced_clock.md). Replies only
    to registered devices whose boot_token matches; everything else is
    discarded without touching any state."""

    def __init__(self, port, clock_us, controller_boot_token):
        self._sock = _udp_listener(port)
        self._clock_us = clock_us
        self._controller_boot_token = controller_boot_token

    @property
    def port(self):
        return self._sock.getsockname()[1]

    def poll(self, boot_token_for):
        while True:
            try:
                datagram, src = self._sock.recvfrom(_UDP_RECV_SIZE)
            except BlockingIOError:
                return
            except OSError as e:
                log.warning('sync: recv failed: %s', e)
                return
            t2 = self._clock_us()
            ping = wire.parse_sync_ping(datagram)
            if ping is None:
                continue
            if boot_token_for(ping.uid) != ping.device_boot_token:
                continue
            t3 = self._clock_us()
            pong = wire.encode_sync_pong(
                self._controller_boot_token, ping.seq, ping.t1_us, t2, t3)
            try:
                self._sock.sendto(pong, src)
            except OSError as e:
                log.debug('sync: PONG to %s failed: %s', src, e)

    def close(self):
        self._sock.close()


class FrameReceiver:
    """Collects sim preview frames; frames from unknown UIDs are noise."""

    def __init__(self, port):
        self._sock = _udp_listener(port)

    @property
    def port(self):
        return self._sock.getsockname()[1]

    def poll(self, is_known_uid):
        frames = []
        while True:
            try:
                datagram, _src = self._sock.recvfrom(_UDP_RECV_SIZE)
            except BlockingIOError:
                return frames
            except OSError as e:
                log.warning('frames: recv failed: %s', e)
                return frames
            frame = wire.parse_frame_preview(datagram)
            if frame is not None and is_known_uid(frame.uid):
                frames.append(frame)

    def close(self):
        self._sock.close()


class _DeviceLink:
    """One registered device connection: the socket, the in-order ACK
    queue, and the idle-ping timer."""

    def __init__(self, sock, reader, uid, boot_token, now_us):
        self.sock = sock
        self.reader = reader
        self.uid = uid
        self.boot_token = boot_token
        self.last_recv_us = now_us
        self.last_send_us = now_us
        self._outbox = bytearray()
        self._pending = []   # (on_ack, sent_at_us), oldest first

    def send(self, encoded, on_ack, now_us):
        self._outbox.extend(encoded)
        self._pending.append((on_ack, now_us))
        self.last_send_us = now_us
        return self.flush()

    def flush(self):
        """Write what the socket will take. False means the link is dead."""
        while self._outbox:
            try:
                sent = self.sock.send(self._outbox)
            except BlockingIOError:
                return True
            except OSError:
                return False
            if sent == 0:
                return False
            del self._outbox[:sent]
        return True

    def on_message(self, opcode, payload, now_us):
        """Dispatch one inbound message; devices only ever send ACKs."""
        if opcode != wire.CMD_ACK:
            raise wire.WireError(f'unexpected opcode from device: 0x{opcode:02x}')
        if not self._pending:
            raise wire.WireError('ACK with no command pending')
        self.last_recv_us = now_us
        on_ack, _sent_at = self._pending.pop(0)
        ack = wire.parse_ack(payload)
        try:
            on_ack(ack)
        except Exception:
            log.exception('%s: ACK callback failed', self.uid)

    def ack_overdue(self, now_us):
        return self._pending and now_us - self._pending[0][1] > ACK_TIMEOUT_US

    def maybe_ping(self, now_us):
        if now_us - self.last_send_us >= PING_INTERVAL_US:
            self.send(wire.encode_ping(), lambda ack: None, now_us)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class _PendingConn:
    """An accepted connection that has not registered yet."""

    def __init__(self, sock, accepted_at_us):
        self.sock = sock
        self.reader = wire.LinkReader()
        self.accepted_at_us = accepted_at_us

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class LinkServer:
    """Accepts device connections and runs the registered links.

    A connection's first message must be a valid REGISTER for a wanted
    UID speaking our protocol version; anything else is closed without
    a reply (silence is the rejection). A second REGISTER for a live
    UID replaces the old connection: the device clearly abandoned it.
    """

    def __init__(self, port):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(('', port))
        self._sock.listen(16)
        self._sock.setblocking(False)
        self._unregistered = []
        self._links = {}            # uid -> _DeviceLink
        self._last_boot_tokens = {}  # uid -> last seen token, kept across disconnects

    @property
    def port(self):
        return self._sock.getsockname()[1]

    def link(self, uid):
        return self._links.get(uid)

    def connected_uids(self):
        return set(self._links)

    def poll(self, now_us, wanted_uids, events):
        self._accept(now_us)
        self._read_unregistered(now_us, wanted_uids, events)
        self._read_links(now_us, events)
        self._drop_stale_unregistered(now_us)
        for uid, link in list(self._links.items()):
            if link.ack_overdue(now_us):
                self._drop(uid, 'ack timeout', events)
                continue
            link.maybe_ping(now_us)
            if not link.flush():
                self._drop(uid, 'send failed', events)

    def close(self):
        self._sock.close()
        for conn in self._unregistered:
            conn.close()
        self._unregistered.clear()
        for link in self._links.values():
            link.close()
        self._links.clear()

    def _accept(self, now_us):
        # loop until the queue is empty (non-blocking accept)
        while True:
            try:
                conn, _addr = self._sock.accept()
            except (BlockingIOError, OSError):
                return
            conn.setblocking(False)
            self._unregistered.append(_PendingConn(conn, now_us))

    @staticmethod
    def _recv_available(sock, reader):
        """Feed everything readable into reader. Returns False on EOF
        or a socket error."""
        while True:
            try:
                data = sock.recv(_TCP_RECV_CHUNK)
            except BlockingIOError:
                return True
            except OSError:
                return False
            if not data:
                return False
            reader.feed(data)

    def _read_unregistered(self, now_us, wanted_uids, events):
        kept = []
        for conn in self._unregistered:
            alive = self._recv_available(conn.sock, conn.reader)
            try:
                messages = conn.reader.messages()
            except wire.WireError:
                conn.close()
                continue
            if messages:
                self._register(conn, messages, now_us, wanted_uids, events)
            elif alive:
                kept.append(conn)
            else:
                conn.close()
        self._unregistered = kept

    def _register(self, conn, messages, now_us, wanted_uids, events):
        opcode, payload = messages[0]
        try:
            if opcode != wire.CMD_REGISTER:
                raise wire.WireError(f'first message must be REGISTER, got 0x{opcode:02x}')
            reg = wire.parse_register(payload)
        except wire.WireError as e:
            log.info('link: rejecting connection: %s', e)
            conn.close()
            return
        if reg.uid not in wanted_uids:
            log.info('link: rejecting unwanted uid %r', reg.uid)
            conn.close()
            return
        if reg.protocol_version != wire.PROTOCOL_VERSION:
            log.info('link: rejecting %s: protocol_version %d (ours: %d)',
                     reg.uid, reg.protocol_version, wire.PROTOCOL_VERSION)
            conn.close()
            return

        if reg.uid in self._links:
            self._drop(reg.uid, 'replaced by new connection', events)

        last_token = self._last_boot_tokens.get(reg.uid)
        rebooted = last_token is not None and last_token != reg.boot_token
        self._last_boot_tokens[reg.uid] = reg.boot_token

        link = _DeviceLink(conn.sock, conn.reader, reg.uid, reg.boot_token, now_us)
        self._links[reg.uid] = link
        events.append(DeviceConnected(uid=reg.uid, boot_token=reg.boot_token,
                                      rebooted=rebooted))
        log.info('link: %s registered (boot_token=%d%s)',
                 reg.uid, reg.boot_token, ', rebooted' if rebooted else '')

        try:
            for extra_opcode, extra_payload in messages[1:]:
                link.on_message(extra_opcode, extra_payload, now_us)
        except wire.WireError as e:
            log.warning('link: %s: %s', reg.uid, e)
            self._drop(reg.uid, 'protocol error', events)

    def _read_links(self, now_us, events):
        for uid, link in list(self._links.items()):
            alive = self._recv_available(link.sock, link.reader)
            try:
                for opcode, payload in link.reader.messages():
                    link.on_message(opcode, payload, now_us)
            except wire.WireError as e:
                log.warning('link: %s: %s', uid, e)
                self._drop(uid, 'protocol error', events)
                continue
            if not alive:
                self._drop(uid, 'closed by device', events)

    def _drop_stale_unregistered(self, now_us):
        kept = []
        for conn in self._unregistered:
            if now_us - conn.accepted_at_us > REGISTER_DEADLINE_S * 1_000_000:
                conn.close()
            else:
                kept.append(conn)
        self._unregistered = kept

    def _drop(self, uid, reason, events):
        link = self._links.pop(uid, None)
        if link is None:
            return
        link.close()
        events.append(DeviceDisconnected(uid=uid, reason=reason))
        log.info('link: %s disconnected: %s', uid, reason)


class DeviceHub:
    """The device-facing half of the controller; see the module doc."""

    def __init__(self, *, discovery_port, link_port, frame_port, sync_port,
                 clock_us=None):
        self._clock_us = clock_us or (lambda: time.monotonic_ns() // 1000)
        self._boot_token = 0
        while self._boot_token == 0:   # 0 is the device's unseeded sentinel
            self._boot_token = random.getrandbits(32)

        self._link_server = LinkServer(link_port)
        self._discovery = DiscoveryServer(discovery_port, self._link_server.port)
        self._frames = FrameReceiver(frame_port)
        self._sync = SyncServer(sync_port, self._clock_us, self._boot_token)

    @property
    def boot_token(self):
        return self._boot_token

    @property
    def discovery_port(self):
        return self._discovery.port

    @property
    def link_port(self):
        return self._link_server.port

    @property
    def frame_port(self):
        return self._frames.port

    @property
    def sync_port(self):
        return self._sync.port

    def poll(self, wanted_uids):
        """One tick: drain all four listeners. Returns HubPoll."""
        now_us = self._clock_us()
        events = []
        self._discovery.poll(wanted_uids)
        self._link_server.poll(now_us, wanted_uids, events)
        self._sync.poll(self._registered_boot_token)
        frames = self._frames.poll(self.is_connected)
        return HubPoll(events=events, frames=frames)

    def send(self, uid, encoded, on_ack=None):
        """Queue one encoded command for uid; on_ack gets the AckMsg.
        False when the device is not connected."""
        link = self._link_server.link(uid)
        if link is None:
            return False
        return link.send(encoded, on_ack or (lambda ack: None), self._clock_us())

    def is_connected(self, uid):
        return self._link_server.link(uid) is not None

    def connected_uids(self):
        return self._link_server.connected_uids()

    def close(self):
        self._discovery.close()
        self._link_server.close()
        self._frames.close()
        self._sync.close()

    def _registered_boot_token(self, uid):
        link = self._link_server.link(uid)
        return link.boot_token if link is not None else None
