import struct

from lib.utils import get_hex_str
from collections import namedtuple

SENSOR_TYPES = {
    'distance'  : 0,
    'button'    : 1,
    'freeheap'  : 2,
}

SENSOR_INTERVAL_MIN_MS = 20
SENSOR_INTERVAL_MAX_MS = 10000

DPIN_MIN = 0
DPIN_MAX = 10

SENSOR_SETUP_KEYS = ['type', 'min_interval_ms', 'publish_raw', 'sampling_window']
DISTANCE_PARAMS_KEYS = ['trig_dpin', 'echo_dpin']

SensorSetupHeader = namedtuple('SensorSetupHeader',
                               ['sensor_type', 'id', 'min_interval_ms', 'publish_raw',
                                'sampling_window'])
SensorDistanceParams = namedtuple('SensorDistanceParams', ['trig_dpin', 'echo_dpin'])


def parse_sensor_distance(params):
    if not all(key in params for key in DISTANCE_PARAMS_KEYS):
        raise ValueError('missing mandatory keys.')

    if params['trig_dpin'] < 0 or params['trig_dpin'] > 10:
        raise ValueError('dpin value oob')
    if params['echo_dpin'] < 0 or params['echo_dpin'] > 10:
        raise ValueError('dpin value oob')

    distance_params = SensorDistanceParams(trig_dpin=params['trig_dpin'],
                                           echo_dpin=params['echo_dpin'])
    distance_params_bytes = struct.pack('<BB', *distance_params)
    return distance_params_bytes


# specific parsers
specific_sensor_parsers = {
    'distance' : parse_sensor_distance,
}



def parse_sensor_config(params, sensor_id):
    if not all(key in params for key in SENSOR_SETUP_KEYS):
        raise ValueError('missing mandatory keys.')

    if params['type'] not in SENSOR_TYPES.keys():
        raise ValueError('invalid sensor type')

    if params['min_interval_ms'] < SENSOR_INTERVAL_MIN_MS or \
        params['min_interval_ms'] > SENSOR_INTERVAL_MAX_MS:
        raise ValueError('min_interval_ms out of bounds')

    if params['publish_raw'] < 0 or params['publish_raw'] > 1000:
        raise ValueError('publish_raw out of bounds')

    setup_header = SensorSetupHeader(sensor_type=SENSOR_TYPES[params['type']],
                                     id=sensor_id,
                                     min_interval_ms=params['min_interval_ms'],
                                     publish_raw=params['publish_raw'],
                                     sampling_window=params['sampling_window'])

    setup_header_bytes = struct.pack('<HHHHB', *setup_header)
    if 'params' in params.keys():
        return setup_header_bytes + specific_sensor_parsers[params['type']](params['params'])
    else:
        return setup_header_bytes


class Element:
    def __init__(self, device_id, mclient):
        self.device_id = device_id
        self.mclient = mclient
        self.sensor_id = 0

    def configure(self, cfg_sensor_list):
        cfg_msg_list = []
        for sensor_cfg in cfg_sensor_list:
            if 'sensor' in sensor_cfg.keys():
                cfg_msg = parse_sensor_config(sensor_cfg['sensor'], self.sensor_id)
                self.sensor_id += 1
                cfg_msg_list.append(cfg_msg)
                print(get_hex_str(cfg_msg))

                topic = f'elements/{self.device_id}/operate/sensors/add'
                self.mclient.publish(topic, cfg_msg, 1, retain=True)
