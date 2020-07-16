from lib.sensor import Sensor
from lib.mclient import MClient
from lib.utils import get_hex_str, validate_all_exist

class SensorManager:

    def __init__(self, mclient):
        self.mclient = mclient
        self.sensor_id = 0
        self.sensors = {}

    def add(self, params):
        validate_all_exist(params.keys(), ['uid_label', 'device_id'])
        device_id = params['device_id']
        uid_label = params['uid_label']

        if uid_label in self.sensors:
            # in case of existing uid_label, only reconfiguring the same sensor
            # to new values is supported. different physical sensors should
            # have different unique labels.
            sensor = self.sensors[uid_label]
            if not sensor.device_id == device_id:
                raise RuntimeError(f"uid_label already in use for {self.sensors['uid_label'].device_id}")

            sensor.update_params(params)
        else:
            # assign a new sensor_id to a new sensor
            sensor = Sensor.create_sensor(self.sensor_id, params, device_id)
            self.sensor_id += 1
            self.sensors[uid_label] = sensor

        # send configuration to device
        sensor.send_configuration(self.mclient)

    def get_sensor(self, uid_label):
        """get sensor by unique id label"""
        if uid_label not in self.sensors:
            raise RuntimeError('uid_label does not exist')

        return self.sensors['uid_label']['sensor']
