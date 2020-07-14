import logging
import random
import time
import sys
import yaml

from context import lib
from lib import logconfig
from lib.utils import printable_bytes, get_hex_str
from lib.mclient import MClient
from lib.sensor import Sensor
from lib.event import Event


def print_cb(prefix):
    print(f"{prefix}: callback!")


logconfig.config_default_logger(level=logging.DEBUG)

server_addr = "192.168.5.5"
device_id = "SIM-00000001"

# initialize mqtt client and connect to server
mclient = MClient(server_addr)

sensor = Sensor(device_id, mclient)

# configure sensor(s) on device
cfg_file = '/home/mickey/dev/elements/controller/test/sim1_cfg.yml'
with open(cfg_file, 'r') as f:
    cfg = yaml.load(f.read())

event_params_file = '/home/mickey/dev/elements/controller/test/inrange2.yml'
with open(event_params_file, 'r') as f:
    event_cfg = yaml.load(f.read())

event_approaching = Event.create_event("in_range", 510, sensor.sensor_id, event_cfg, print_cb, "my prefix")
print(event_approaching)