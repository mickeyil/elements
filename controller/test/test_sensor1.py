import logging
import random
import time
import yaml
import paho.mqtt.publish as publish

from context import lib
from lib import logconfig
from lib.utils import printable_bytes, get_hex_str
from lib.mclient import MClient
from lib.element import Element

server_addr = "192.168.5.5"

#device_id = 'ESP-000B1277'
#device_id = 'ESP-000DC374'
#device_id2 = 'ESP-000B11DB'
device_id_3 = 'ESP-000DC374'
device_id_sim = 'SIM-00000001'
mclient = MClient(server_addr)
sim = Element(device_id_sim, mclient)

cfg_file = '/home/mickey/dev/elements/controller/test/sim1_cfg.yml'
with open(cfg_file, 'r') as f:
    cfg = yaml.load(f.read())

print(cfg)
sim.configure(cfg)
