"""v3 device wire codecs: bytes in, bytes out, no sockets.

One function per packet or command, shared by everything that talks to
devices. The C++ device is the reference implementation; the layouts
here mirror it byte for byte:

  - TCP link framing and opcodes: src/link_protocol.h,
    src/controller_link.cpp (REGISTER), src/command_handler.cpp
    (ACK payloads). Reference: drafts/controller_v3.md appendices.
  - Discovery DISCOVER/OFFER: src/discovery.cpp.
  - Clock sync PING/PONG: src/clock_sync_client.cpp,
    drafts/synced_clock.md appendix A.
  - Sim frame previews: src/sim/sim_frame_output.h.

All multi-byte fields are little-endian except the OFFER's IPv4
address, which is four octets in network order. TCP messages are
length-prefixed: [length: u32][opcode: u8][payload], where length
counts opcode + payload but not itself.

Conventions: encoders take plain Python values (str names and uids,
int ports and times, bytes blobs) and return framed wire bytes;
parsers take raw payload or datagram bytes. Times ending in _us are
integer microseconds on the controller's monotonic clock; t_program
is float seconds.

TCP parsers raise WireError on malformed input (a corrupt stream is a
protocol violation; the caller drops the connection). UDP parsers
return None instead (random datagrams on a well-known port are not an
event worth handling).
"""

import math
import socket
import struct
from dataclasses import dataclass

# ---- Constants mirroring src/link_protocol.h --------------------------------

PROTOCOL_VERSION = 3

CMD_REGISTER             = 0x00
CMD_SET_PROFILE          = 0x01
CMD_LOAD                 = 0x10
CMD_START                = 0x11
CMD_JUMP                 = 0x12
CMD_PAUSE                = 0x13
CMD_RESUME               = 0x14
CMD_STOP                 = 0x15
CMD_PLAY_LOCAL_ANIMATION = 0x16
CMD_STORE_ANIMATION      = 0x20
CMD_ERASE_ANIMATION      = 0x21
CMD_SET_ANIMATION_ORDER  = 0x22
CMD_REBOOT               = 0x30
CMD_QUERY_DEVICE_STATUS  = 0x40
CMD_PING                 = 0x41
CMD_QUERY_LOCAL_ANIMATIONS = 0x42
CMD_ACK                  = 0x80

ACK_OK               = 0
ACK_ERROR            = 1
ACK_WRONG_STATE      = 2
ACK_PROFILE_MISMATCH = 3
ACK_BAD_PAYLOAD      = 4
ACK_UNKNOWN_COMMAND  = 5
ACK_UNSYNCED         = 6

ACK_STATUS_NAMES = {
    ACK_OK: 'ok',
    ACK_ERROR: 'error',
    ACK_WRONG_STATE: 'wrong_state',
    ACK_PROFILE_MISMATCH: 'profile_mismatch',
    ACK_BAD_PAYLOAD: 'bad_payload',
    ACK_UNKNOWN_COMMAND: 'unknown_command',
    ACK_UNSYNCED: 'unsynced',
}

PING_INTERVAL_MS = 5_000

MAX_BLOB_BYTES = 16 * 1024
TCP_MSG_MAX = MAX_BLOB_BYTES + 256

# Mirrors src/device_identity.h and src/animation_store.h.
UID_SIZE = 16
ANIM_NAME_SIZE = 32

# Device mode byte in the QueryDeviceStatus ACK (src/device_status.h).
DEVICE_MODE_NAMES = {
    0: 'attached_controlled',
    1: 'detached_grace_hold',
    2: 'detached_blank',
    3: 'detached_background',
}
STATUS_FLAG_PROFILE_PRESENT = 0x01

# ---- Constants mirroring src/discovery.cpp ----------------------------------

DISCOVERY_MAGIC = 0xD1CC
PKT_DISCOVER = 0x01
PKT_OFFER = 0x02
DISCOVER_WIRE_SIZE = 2 + 1 + UID_SIZE
OFFER_WIRE_SIZE = 2 + 1 + 4 + 2

# ---- Constants mirroring src/clock_sync_client.cpp --------------------------

SYNC_PKT_PING = 0x01
SYNC_PKT_PONG = 0x02
SYNC_PING_WIRE_SIZE = 33
SYNC_PONG_WIRE_SIZE = 33

# ---- Constants mirroring src/sim/sim_frame_output.h --------------------------

FRAME_PREVIEW_HEADER_BYTES = UID_SIZE + 4 + 4

# ---- Constants mirroring src/log_shipper.cpp ---------------------------------

LOG_MAGIC = 0xD16C
LOG_VERSION = 1
LOG_HEADER_BYTES = 2 + 1 + UID_SIZE + 4 + 4 + 4 + 1
# Mirrors SLOG_TEXT_CAP in src/slog.h; anything longer is a forgery.
LOG_TEXT_CAP = 224


class WireError(ValueError):
    """Raised for bytes that violate the protocol, or values that cannot
    be encoded onto it."""


# ---- Struct formats ----------------------------------------------------------

_MSG_HEADER = struct.Struct('<IB')                  # length + opcode
_U16 = struct.Struct('<H')
_I64 = struct.Struct('<q')
_F32 = struct.Struct('<f')
_REGISTER = struct.Struct(f'<{UID_SIZE}sIB')        # uid + boot_token + version
_DEVICE_STATUS = struct.Struct('<BBH')              # mode + flags + animation_count
_ANIM_RECORD = struct.Struct(f'<{ANIM_NAME_SIZE}sHI')  # name + strip_length + crc32
_DISCOVER = struct.Struct(f'<HB{UID_SIZE}s')        # magic + type + uid
_OFFER_PREFIX = struct.Struct('<HB')                # magic + type; then ipv4 + port
_SYNC_PING = struct.Struct(f'<B{UID_SIZE}sIIq')     # type + uid + boot_token + seq + t1
_SYNC_PONG = struct.Struct('<BIIqqq')               # type + token + seq + t1 + t2 + t3
_FRAME_PREVIEW = struct.Struct(f'<{UID_SIZE}sIf')   # uid + frame_index + t_program
_LOG_HEADER = struct.Struct(f'<HB{UID_SIZE}sIIIB')  # magic + version + uid
                                                    # + boot_token + seq
                                                    # + uptime_ms + level


# ---- Slot helpers ------------------------------------------------------------

def _pack_slot(text, size, what):
    """Encode text into a fixed-size ASCII slot, null-padded if shorter."""
    try:
        raw = text.encode('ascii')
    except UnicodeEncodeError as e:
        raise WireError(f'{what} must be ASCII: {text!r}') from e
    if not raw:
        raise WireError(f'{what} must be non-empty')
    if len(raw) > size:
        raise WireError(f'{what} longer than {size} bytes: {text!r}')
    if b'\x00' in raw:
        raise WireError(f'{what} must not contain NUL: {text!r}')
    return raw.ljust(size, b'\x00')


def _unpack_slot(raw, what):
    """Decode a null-padded ASCII slot; a full-width value has no padding."""
    text = raw.split(b'\x00', 1)[0]
    if not text:
        raise WireError(f'{what} slot is empty')
    if not all(0x20 <= b <= 0x7E for b in text):
        raise WireError(f'{what} slot is not printable ASCII: {raw!r}')
    return text.decode('ascii')


def _require_finite(value, what):
    value = float(value)
    if not math.isfinite(value):
        raise WireError(f'{what} must be finite, got {value!r}')
    return value


def _require_u16(value, what):
    if not (0 <= value <= 0xFFFF):
        raise WireError(f'{what} must fit in u16, got {value}')
    return value


# ---- TCP framing ---------------------------------------------------------------

def encode_message(opcode, payload=b''):
    """Frame one link message: [length:u32][opcode:u8][payload]."""
    if 1 + len(payload) > TCP_MSG_MAX:
        raise WireError(f'message too large: {1 + len(payload)} > {TCP_MSG_MAX}')
    return _MSG_HEADER.pack(1 + len(payload), opcode) + payload


class LinkReader:
    """Incremental parser for the length-prefixed link stream.

    feed() bytes as they arrive; messages() drains complete
    (opcode, payload) pairs. Raises WireError on an impossible length;
    the stream is unrecoverable after that and the caller must drop
    the connection.
    """

    def __init__(self):
        self._buf = bytearray()

    def feed(self, data):
        self._buf.extend(data)

    def messages(self):
        out = []
        while len(self._buf) >= 4:
            length = int.from_bytes(self._buf[:4], 'little')
            if length < 1 or length > TCP_MSG_MAX:
                raise WireError(f'invalid message length: {length}')
            if len(self._buf) < 4 + length:
                break
            opcode = self._buf[4]
            payload = bytes(self._buf[5:4 + length])
            del self._buf[:4 + length]
            out.append((opcode, payload))
        return out


# ---- Command encoders (controller to device) ----------------------------------

def encode_set_profile(strip_length):
    _require_u16(strip_length, 'strip_length')
    return encode_message(CMD_SET_PROFILE, _U16.pack(strip_length))


def encode_load(blob):
    if not blob:
        raise WireError('blob must be non-empty')
    if len(blob) > MAX_BLOB_BYTES:
        raise WireError(f'blob too large: {len(blob)} > {MAX_BLOB_BYTES}')
    return encode_message(CMD_LOAD, bytes(blob))


def encode_start(program_start_us):
    return encode_message(CMD_START, _I64.pack(program_start_us))


# JUMP repositions playback onto a compiler-marked safe interval. Retained for
# future seek and live rejoin; the v3 session does not issue it (drafts/jump.md).
def encode_jump(t_program):
    return encode_message(CMD_JUMP, _F32.pack(_require_finite(t_program, 't_program')))


def encode_pause():
    return encode_message(CMD_PAUSE)


def encode_resume(program_start_us):
    return encode_message(CMD_RESUME, _I64.pack(program_start_us))


def encode_stop():
    return encode_message(CMD_STOP)


def encode_play_local_animation(order_index):
    _require_u16(order_index, 'order_index')
    return encode_message(CMD_PLAY_LOCAL_ANIMATION, _U16.pack(order_index))


def encode_store_animation(name, blob):
    """Write a blob into the device's local animation store under name."""
    slot = _pack_slot(name, ANIM_NAME_SIZE, 'animation name')
    if not blob:
        raise WireError('blob must be non-empty')
    if len(blob) > MAX_BLOB_BYTES:
        raise WireError(f'blob too large: {len(blob)} > {MAX_BLOB_BYTES}')
    return encode_message(CMD_STORE_ANIMATION, slot + bytes(blob))


def encode_erase_animation(name):
    return encode_message(CMD_ERASE_ANIMATION,
                          _pack_slot(name, ANIM_NAME_SIZE, 'animation name'))


def encode_set_animation_order(names):
    """Replace the device's play order with names, first to last."""
    _require_u16(len(names), 'animation count')
    slots = b''.join(_pack_slot(n, ANIM_NAME_SIZE, 'animation name') for n in names)
    return encode_message(CMD_SET_ANIMATION_ORDER, _U16.pack(len(names)) + slots)


def encode_reboot():
    return encode_message(CMD_REBOOT)


def encode_query_device_status():
    return encode_message(CMD_QUERY_DEVICE_STATUS)


def encode_ping():
    return encode_message(CMD_PING)


def encode_query_local_animations():
    return encode_message(CMD_QUERY_LOCAL_ANIMATIONS)


# ---- Inbound TCP parsers (device to controller) --------------------------------

@dataclass(frozen=True)
class RegisterMsg:
    uid: str
    boot_token: int
    protocol_version: int


@dataclass(frozen=True)
class AckMsg:
    status: int
    payload: bytes


@dataclass(frozen=True)
class DeviceStatusReport:
    mode: int                # raw byte; DEVICE_MODE_NAMES maps known values
    profile_present: bool
    animation_count: int


@dataclass(frozen=True)
class LocalAnimationRecord:
    name: str
    strip_length: int
    crc32: int


def parse_register(payload):
    """Decode the REGISTER a device sends right after connecting
    into a RegisterMsg.

    Accepting (keep talking) or rejecting (silently close) is the
    caller's decision; there is no ACK for REGISTER.
    """
    if len(payload) != _REGISTER.size:
        raise WireError(f'REGISTER payload must be {_REGISTER.size} bytes, '
                        f'got {len(payload)}')
    uid_raw, boot_token, version = _REGISTER.unpack(payload)
    return RegisterMsg(
        uid=_unpack_slot(uid_raw, 'uid'),
        boot_token=boot_token,
        protocol_version=version,
    )


def parse_ack(payload):
    """Split an ACK into an AckMsg (status, payload). Treat any non-Ok
    status as the command not having happened."""
    if len(payload) < 1:
        raise WireError('ACK payload must carry a status byte')
    return AckMsg(status=payload[0], payload=payload[1:])


def parse_device_status(payload):
    """Decode the QueryDeviceStatus ACK payload into a DeviceStatusReport."""
    if len(payload) != _DEVICE_STATUS.size:
        raise WireError(f'device status payload must be {_DEVICE_STATUS.size} '
                        f'bytes, got {len(payload)}')
    mode, flags, animation_count = _DEVICE_STATUS.unpack(payload)
    return DeviceStatusReport(
        mode=mode,
        profile_present=bool(flags & STATUS_FLAG_PROFILE_PRESENT),
        animation_count=animation_count,
    )


def parse_local_animations(payload):
    """Decode the QueryLocalAnimations ACK payload into
    LocalAnimationRecords, in play order.

    The crc32 is over the whole blob; compare it against the
    controller's own artifact to spot a stale stored animation.
    """
    if len(payload) < 2:
        raise WireError('local animations payload must carry a count')
    (count,) = _U16.unpack_from(payload, 0)
    expected = 2 + count * _ANIM_RECORD.size
    if len(payload) != expected:
        raise WireError(f'local animations payload must be {expected} bytes '
                        f'for count {count}, got {len(payload)}')
    records = []
    for i in range(count):
        name_raw, strip_length, crc32 = _ANIM_RECORD.unpack_from(
            payload, 2 + i * _ANIM_RECORD.size)
        records.append(LocalAnimationRecord(
            name=_unpack_slot(name_raw, 'animation name'),
            strip_length=strip_length,
            crc32=crc32,
        ))
    return records


# ---- Discovery (UDP 6040) ------------------------------------------------------

def parse_discover(datagram):
    """Return the broadcasting device's UID, or None for unrelated traffic."""
    if len(datagram) != DISCOVER_WIRE_SIZE:
        return None
    magic, pkt_type, uid_raw = _DISCOVER.unpack(datagram)
    if magic != DISCOVERY_MAGIC or pkt_type != PKT_DISCOVER:
        return None
    try:
        return _unpack_slot(uid_raw, 'uid')
    except WireError:
        return None


def encode_offer(ipv4, tcp_port):
    """OFFER telling a device where the controller's link server listens."""
    try:
        ip_bytes = socket.inet_aton(ipv4)
    except OSError as e:
        raise WireError(f'invalid IPv4 address: {ipv4!r}') from e
    _require_u16(tcp_port, 'tcp_port')
    if tcp_port == 0:
        raise WireError('tcp_port must be non-zero')
    return (_OFFER_PREFIX.pack(DISCOVERY_MAGIC, PKT_OFFER)
            + ip_bytes + _U16.pack(tcp_port))


# ---- Clock sync (UDP 6043) -----------------------------------------------------

@dataclass(frozen=True)
class SyncPing:
    uid: str
    device_boot_token: int
    seq: int
    t1_us: int


def parse_sync_ping(datagram):
    """Decode one sync request into a SyncPing; seq and t1_us must be
    echoed in the PONG."""
    if len(datagram) != SYNC_PING_WIRE_SIZE:
        return None
    pkt_type, uid_raw, boot_token, seq, t1_us = _SYNC_PING.unpack(datagram)
    if pkt_type != SYNC_PKT_PING:
        return None
    try:
        uid = _unpack_slot(uid_raw, 'uid')
    except WireError:
        return None
    return SyncPing(uid=uid, device_boot_token=boot_token, seq=seq, t1_us=t1_us)


def encode_sync_pong(controller_boot_token, seq, t1_us, t2_us, t3_us):
    """Reply to a PING. t2/t3 must come from the same monotonic clock
    that stamps program_start_us; that clock is what devices sync to."""
    if controller_boot_token == 0:
        raise WireError('controller_boot_token must be non-zero '
                        '(0 is the device unseeded sentinel)')
    return _SYNC_PONG.pack(SYNC_PKT_PONG, controller_boot_token, seq,
                           t1_us, t2_us, t3_us)


# ---- Sim frame previews (UDP 6042) ----------------------------------------------

@dataclass(frozen=True)
class FramePreview:
    uid: str
    frame_index: int
    t_program: float
    rgb: bytes


def parse_frame_preview(datagram):
    """Decode one sim preview frame into a FramePreview; rgb covers the
    whole strip, three bytes per pixel. Gaps in frame_index mean
    dropped packets."""
    if len(datagram) < FRAME_PREVIEW_HEADER_BYTES:
        return None
    uid_raw, frame_index, t_program = _FRAME_PREVIEW.unpack_from(datagram, 0)
    rgb = datagram[FRAME_PREVIEW_HEADER_BYTES:]
    if not rgb or len(rgb) % 3 != 0:
        return None
    try:
        uid = _unpack_slot(uid_raw, 'uid')
    except WireError:
        return None
    return FramePreview(uid=uid, frame_index=frame_index,
                        t_program=t_program, rgb=bytes(rgb))


# ---- Device logs (UDP 6044) --------------------------------------------------

_LOG_LEVELS = frozenset('IWE')


@dataclass(frozen=True)
class LogRecord:
    uid: str
    boot_token: int
    seq: int          # per boot, monotonically increasing from 1
    uptime_ms: int    # device clock at log time; arrival is stamped here
    level: str        # 'I' | 'W' | 'E'
    text: str


def parse_log_record(datagram):
    """Decode one device log datagram into a LogRecord; None for
    anything malformed. Gaps in seq within one boot_token mean lost
    records (ring overflow on the device, or packets dropped).

    The uid is unauthenticated, so the text is defanged here: clamped
    to the device's cap and stripped of control characters, keeping a
    spoofed packet from injecting newlines or terminal escapes into
    the controller's log."""
    if len(datagram) < LOG_HEADER_BYTES:
        return None
    (magic, version, uid_raw, boot_token, seq, uptime_ms,
     level) = _LOG_HEADER.unpack_from(datagram, 0)
    if magic != LOG_MAGIC or version != LOG_VERSION:
        return None
    level_ch = chr(level)
    if level_ch not in _LOG_LEVELS:
        return None
    try:
        uid = _unpack_slot(uid_raw, 'uid')
    except WireError:
        return None
    raw = datagram[LOG_HEADER_BYTES:LOG_HEADER_BYTES + LOG_TEXT_CAP]
    text = raw.decode('utf-8', errors='replace')
    text = ''.join(ch if ch.isprintable() else '\ufffd' for ch in text)
    return LogRecord(uid=uid, boot_token=boot_token, seq=seq,
                     uptime_ms=uptime_ms, level=level_ch, text=text)
