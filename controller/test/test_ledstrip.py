#!/usr/bin/env python3

import sys

from context import lib
import logging
from lib import logconfig
logconfig.config_default_logger()

import numpy as np
from lib.ledstrip import ledstrip

strip1 = ledstrip(length=10, device_id='ESP-00147D0A')
strip1.update()
#strip1.setall([0, 0, 255])
#strip1.update()
logging.info('start loop')
for j in range(1000):
    for i in range(256):
        logging.info(f'sending {i}')
        strip1.setall([0, 0, i])
        strip1.update()

