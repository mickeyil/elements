#!/usr/bin/env python3

import sys

from context import lib
import logging
from lib import logconfig
logconfig.config_default_logger()

import numpy as np
from lib.sensors import SensorDistance

strip1 = SensorDistance(device_id='ESP-00147633')

