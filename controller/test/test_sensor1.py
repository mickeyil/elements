import logging
import random
import time
import sys
import yaml
import paho.mqtt.publish as publish

from context import lib
from lib import logconfig
import logging

from lib.utils import printable_bytes, get_hex_str
from lib.mclient import MClient
from lib.sensor import Sensor
from lib.event import parse_event_config

logconfig.config_default_logger(level=logging.DEBUG)

server_addr = "192.168.5.5"

device_id_3 = 'ESP-000B1277'
device_id_sim = 'SIM-00000001'
mclient = MClient(server_addr)
sim = Sensor(device_id_sim, mclient)

cfg_file = '/home/mickey/dev/elements/controller/test/sim1_cfg.yml'
with open(cfg_file, 'r') as f:
    cfg = yaml.load(f.read())

print(cfg)
sim.configure(cfg)

event_file = '/home/mickey/dev/elements/controller/test/inrange1.yml'
with open(event_file, 'r') as f:
    event_cfg = yaml.load(f.read())
event_add_bytes = parse_event_config(event_cfg[0]['event'], 510)

print(get_hex_str(event_add_bytes))
topic = f'elements/{device_id_sim}/operate/events/add'

mclient.publish(topic, event_add_bytes, 1, False)
mclient.loop(timeout=0.1)