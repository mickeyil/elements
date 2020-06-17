import logging
import random
import struct
from lib.utils import printable_bytes, get_hex_str

import numpy as np
from collections import namedtuple
import paho.mqtt.publish as publish

# address of mqtt server
server_addr = "192.168.5.5"


MsgHeader = namedtuple('MsgHeader', ['req_id', 'pin', 'data_size'])

"""
RGBUnit uses rgb_array to represent the RGB values that are sent to the device.
rgb_array is a Nx3 numpy array representing the R,G,B such that:

    R G B
  1
  2
  3
  .
  .
  .
  N
"""


class RGBUnit:

    def __init__(self, device_id):
        self.client_id = f"i-rgbunit-{random.randint(0,1e8-1):08d}"
        self.topic_set = f"elements/{device_id}/operate/rgb/set"
        # request id is a sequence for debug purposes.
        self.req_id = 1

    def set(self, rgb_array):
        if not isinstance(rgb_array, np.ndarray):
            raise ValueError('rgb_array must be a numpy array')
        if not rgb_array.dtype == np.uint8:
            raise ValueError('rgb_array must be of type uint8')
        # rgb_array dimensions should be Nx3
        if not (rgb_array.ndim == 2 and rgb_array.shape[1] == 3):
            raise ValueError(f'invalid rgb_array dimensions: {str(rgb_array.shape)}')

        num_pixels = rgb_array.shape[0]
        data_size = 3*num_pixels

        msg_header = MsgHeader(req_id=self.req_id, pin=3, data_size=data_size)
        rgb_array_bin = rgb_array.tobytes()
        req_msg = struct.pack("<III", *msg_header) + rgb_array.tobytes()

        logging.debug(f'[{self.req_id}] sending: set {num_pixels} pixels to '
                      f'{self.topic_set}: {get_hex_str(rgb_array_bin)}')

        publish.single(self.topic_set, payload=req_msg, qos=1, retain=True,
                       hostname=server_addr, client_id=self.client_id)

        self.req_id += 1
