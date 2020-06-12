#!/usr/bin/env python3

import sys

from context import lib

import numpy as np
from lib.ledstrip import ledstrip

strip1 = ledstrip(length=10, device_id='ESP-000BA624')
strip1.update()
strip1.setall([0, 0, 255])
strip1.update()

