#!/usr/bin/env python3

import sys

from context import lib

import numpy as np
from lib.rgbunit import RGBUnit

A = [0, 0, 255]
B = [0, 255, 0]
C = [255, 0, 0]

rgb_array = np.array([A, B, C]).astype('uint8')

#rgbunit = RGBUnit('MOCK-1')
rgbunit = RGBUnit('ESP-000BA624')
rgbunit.set(pin=10, rgb_array=rgb_array)
