"""Wire protocol encoding/decoding for the device transport layer.

All multi-byte fields are little-endian. Commands are length-prefixed:
    [length: u32] [type: u8] [payload...]
where length includes the type byte but not itself.
"""

from __future__ import annotations

import struct

from .device import DeviceFrame

# Command type constants
SYNC_REQ = 0x01
SYNC_RESP = 0x02
CMD_SYNC_RESULT = 0x03
CMD_SET_PROFILE = 0x05
CMD_ATTACH = 0x06
CMD_LOAD = 0x10
CMD_START = 0x11
CMD_JUMP = 0x12
CMD_PAUSE = 0x13
CMD_RESUME = 0x14
CMD_STOP = 0x15
CMD_REBOOT = 0x30
CMD_DEBUG_SEEK = 0x22
CMD_ACK = 0x80

# UDP frame header: device_id(u16) + gen(u16) + frame_index(u32) + t_rel(f32)
UDP_FRAME_HEADER = struct.Struct('<HHIf')
SYNC_REQ_STRUCT = struct.Struct('<BHIq')
SYNC_RESP_STRUCT = struct.Struct('<BHIqqq')


def encode_load(gen: int, blob: bytes) -> bytes:
    payload = struct.pack('<H', gen) + blob
    return struct.pack('<IB', 1 + len(payload), CMD_LOAD) + payload


def encode_set_profile(strip_length: int) -> bytes:
    return struct.pack('<IBH', 3, CMD_SET_PROFILE, strip_length)


def encode_attach(device_id: int, frame_port: int) -> bytes:
    return struct.pack('<IBHH', 5, CMD_ATTACH, device_id, frame_port)


def encode_sync_req(seq: int, boot_token: int, t1_us: int) -> bytes:
    return SYNC_REQ_STRUCT.pack(SYNC_REQ, seq, boot_token, t1_us)


def encode_sync_result(seq: int, boot_token: int, offset_us: int) -> bytes:
    return struct.pack('<IBHIq', 15, CMD_SYNC_RESULT, seq, boot_token, offset_us)


def encode_start(t0_us: int) -> bytes:
    return struct.pack('<IBq', 9, CMD_START, t0_us)


def encode_jump(t0_us: int, t_rel: float, gen: int) -> bytes:
    return struct.pack('<IBqfH', 15, CMD_JUMP, t0_us, t_rel, gen)


def encode_pause() -> bytes:
    return struct.pack('<IB', 1, CMD_PAUSE)


def encode_resume(t0_us: int) -> bytes:
    return struct.pack('<IBq', 9, CMD_RESUME, t0_us)


def encode_stop() -> bytes:
    return struct.pack('<IB', 1, CMD_STOP)


def encode_reboot() -> bytes:
    return struct.pack('<IB', 1, CMD_REBOOT)


def encode_debug_seek(t_rel: float) -> bytes:
    return struct.pack('<IBf', 5, CMD_DEBUG_SEEK, t_rel)


def parse_ack(data: bytes) -> int | None:
    """Parse a length-prefixed ACK message. Returns status byte or None."""
    if len(data) < 6:  # 4 (length) + 1 (type) + 1 (status)
        return None
    length = struct.unpack_from('<I', data, 0)[0]
    if length < 2 or len(data) < 4 + length:
        return None
    if data[4] != CMD_ACK:
        return None
    return data[5]


def parse_sync_resp(data: bytes) -> tuple[int, int, int, int, int] | None:
    """Parse a SYNC_RESP datagram.

    Returns (seq, boot_token, t1_us, t2_us, t3_us) or None.
    """
    if len(data) != SYNC_RESP_STRUCT.size:
        return None
    pkt_type, seq, boot_token, t1_us, t2_us, t3_us = SYNC_RESP_STRUCT.unpack(data)
    if pkt_type != SYNC_RESP:
        return None
    return seq, boot_token, t1_us, t2_us, t3_us


def parse_udp_frame(data: bytes) -> tuple[int, DeviceFrame] | None:
    """Parse a UDP frame datagram. Returns (device_id, DeviceFrame) or None."""
    if len(data) < UDP_FRAME_HEADER.size:
        return None
    device_id, gen, frame_index, t_rel = UDP_FRAME_HEADER.unpack_from(data, 0)
    rgb = data[UDP_FRAME_HEADER.size:]
    return device_id, DeviceFrame(
        gen=gen,
        frame_index=frame_index,
        t_rel=t_rel,
        rgb=bytes(rgb),
    )
