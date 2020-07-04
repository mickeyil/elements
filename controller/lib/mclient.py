import paho.mqtt.publish

class MClient:
    def __init__(self, server_addr, client_id="mclient-1"):
        self.server_addr = server_addr
        self.client_id = client_id

    def publish(self, topic, payload, qos, retain):
        paho.mqtt.publish.single(topic, payload=payload, qos=qos, retain=retain,
            hostname=self.server_addr, client_id=self.client_id)
