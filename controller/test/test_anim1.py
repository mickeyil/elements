import logging
import random
import time
import yaml
import paho.mqtt.publish as publish

from context import lib
from lib import logconfig
from lib.utils import printable_bytes, get_hex_str
from lib.parser import Parser

server_addr = "192.168.5.5"

device_id = 'ESP-000B1277'
client_id = f"i-testanim1-{random.randint(0, 1e8 - 1):08d}"
topic_setup = f"elements/{device_id}/operate/animation/setup"

logconfig.config_default_logger()

YAML_FILE = '/home/mickey/dev/elements/controller/test/lights_on.yml'
with open(YAML_FILE, 'r') as f:
    plans_file = f.read()

plans = yaml.load(plans_file)
parser = Parser()

msgs_bytes = parser.parse(time.time(), plans)

for m in msgs_bytes:
    print(get_hex_str(m))
    publish.single(topic_setup, payload=m, qos=1, retain=False,
                   hostname=server_addr, client_id=client_id)
