import time
import yaml

from context import lib
import logging
from lib import logconfig
from lib.parser import Parser
from lib.utils import get_hex_str

logconfig.config_default_logger()

YAML_FILE = '/home/mickey/dev/elements/controller/test/lights_on.yml'
with open(YAML_FILE, 'r') as f:
    plans_file = f.read()

plans = yaml.load(plans_file)
parser = Parser()

msgs_bytes = parser.parse(time.time(), plans)
for m in msgs_bytes:
    print(get_hex_str(m))
