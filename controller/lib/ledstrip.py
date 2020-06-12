import numpy as np
from lib.rgbunit import RGBUnit

class ledstrip:

    def __init__(self, length, device_id, **kwargs):
        self.length = length
        self.layout = kwargs.get('layout', 'RGB')
        self.rgbunit = RGBUnit(device_id)

        self.rgbarr = np.zeros((length, 3), dtype='uint8')

    def setall(self, rgb):
        self.rgbarr = np.tile(np.array(rgb, dtype='uint8'), (self.length, 1))

    def update(self):
        self.rgbunit.set(rgb_array=self.rgbarr)
