import array
import struct
from lib.utils import validate_in_list

STRIP_TYPES = {
    "STRIP_RGB": 0,
    "STRIP_BGR": 1,
}


class Strip:
    def __init__(self, length, strip_type):
        self.length = length
        validate_in_list([strip_type], STRIP_TYPES.keys())
        self.strip_type = strip_type

    def serialize(self):
        header_bytes = struct.pack("<BB", STRIP_TYPES[self.strip_type], self.length)
        msg = header_bytes + array.array('B', list(range(self.length)))
        return msg

    def cfg_topic(device_id):
        return f"elements/{device_id}/operate/animation/setup"