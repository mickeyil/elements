"""Controller-protocol framing: encode, then read back through the reader."""

from elemctl.controller_protocol import (
    KIND_DEVICE_FRAME,
    ProtocolReader,
    encode_device_frame,
    parse_device_frame_payload,
)


def test_device_frame_round_trip():
    rgb = bytes(range(90))
    reader = ProtocolReader()
    reader.feed(encode_device_frame('sim-ä', rgb))   # multi-byte uid: length is in bytes
    [(kind, payload)] = reader.messages()
    assert kind == KIND_DEVICE_FRAME
    assert parse_device_frame_payload(payload) == ('sim-ä', rgb)
