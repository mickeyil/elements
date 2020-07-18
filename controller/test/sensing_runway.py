import logging
import random
import time
import sys
import yaml

from context import lib
from lib import logconfig
from lib.utils import printable_bytes, get_hex_str
from lib.mclient import MClient
from lib.event import Event
from lib.sensor_manager import SensorManager
from lib.event_manager import EventManager

def print_cb(prefix):
    print(f"{prefix}: callback!")


logconfig.config_default_logger(level=logging.DEBUG)

server_addr = "192.168.5.5"

# initialize mqtt client and connect to server
mclient = MClient(server_addr)

sensor_manager = SensorManager(mclient)

# configure sensor(s) on device
sensor_cfg_file = '/home/mickey/dev/elements/controller/test/sensor1.yml'
with open(sensor_cfg_file, 'r') as f:
    sensor_cfg = yaml.safe_load(f.read())

sensor_manager.add(sensor_cfg)
sensor_owl = sensor_manager.get_sensor("owl")

event_params_file = '/home/mickey/dev/elements/controller/test/inrange2.yml'
with open(event_params_file, 'r') as f:
    event_cfg = yaml.safe_load(f.read())

event_manager = EventManager(mclient)
event_manager.add_trigger(event_cfg)
event_manager.add_event("in medium range", sensor_owl, print_cb, "in medium range")
event_manager.subscribe_to_events()

while True:
    mclient.loop(timeout=1.0)
    print(".",end="", flush=True)


'''

event_params_file = '/home/mickey/dev/elements/controller/test/inrange2.yml'
with open(event_params_file, 'r') as f:
    event_cfg = yaml.safe_load(f.read())

event_approaching = Event.create_event("in_range", 510, sensor.sensor_id, event_cfg, print_cb, "my prefix")
print(get_hex_str(event_approaching.compile()))

# next:
# add some kind of event manager, connecting event parameters with a sensor instance
# resolve meaning of sensor at sensor class. It actually configures multiple sensors.
# design animation class?
'''