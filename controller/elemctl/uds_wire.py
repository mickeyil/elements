"""UDS wire protocol encoding/decoding for controller ↔ client communication.

Wire format: [u32 LE length][u8 kind][payload]
where length covers kind + payload (not itself).
"""

from __future__ import annotations

import json
import struct

KIND_JSON = 0x01   # UTF-8 JSON
KIND_FRAME = 0x02  # binary program frame

_HEADER = struct.Struct('<IB')  # length(u32) + kind(u8)
_FRAME_HEADER = struct.Struct('<If')  # frame_index(u32) + t_rel(f32)


def encode_json(obj: dict) -> bytes:
    """Encode a JSON-serializable dict as a length-prefixed UDS message."""
    payload = json.dumps(obj, separators=(',', ':')).encode('utf-8')
    return _HEADER.pack(1 + len(payload), KIND_JSON) + payload


def encode_frame(frame_index: int, t_rel: float, strips: list[bytes]) -> bytes:
    """Encode a program frame as a length-prefixed UDS message.

    Payload: [u32 frame_index][f32 t_rel][rgb_0]...[rgb_N-1]
    """
    body = _FRAME_HEADER.pack(frame_index, t_rel)
    for rgb in strips:
        body += rgb
    return _HEADER.pack(1 + len(body), KIND_FRAME) + body


def parse_json_payload(payload: bytes) -> dict:
    """Decode a KIND_JSON payload to a dict."""
    return json.loads(payload.decode('utf-8'))


class UdsReader:
    """Incremental parser for the UDS wire format.

    Handles partial reads — buffers incomplete messages internally.
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> None:
        """Accumulate data from recv()."""
        self._buf.extend(data)

    def messages(self) -> list[tuple[int, bytes]]:
        """Drain complete (kind, payload) pairs from the buffer."""
        out: list[tuple[int, bytes]] = []
        while len(self._buf) >= _HEADER.size:
            length = struct.unpack_from('<I', self._buf, 0)[0]
            if length < 1:
                # Invalid frame (no kind byte). Discard the 4-byte length
                # field to resynchronize.
                del self._buf[:4]
                continue
            total = 4 + length  # 4 bytes for the length field itself
            if len(self._buf) < total:
                break
            kind = self._buf[4]
            payload = bytes(self._buf[5:total])
            del self._buf[:total]
            out.append((kind, payload))
        return out
