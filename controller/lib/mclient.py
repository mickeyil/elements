import random
import logging

import paho.mqtt.publish
import paho.mqtt.client as mqtt

from lib.utils import get_hex_str
# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, rc):
    logging.info("Connected with result code "+str(rc))

    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.

    # key value: topic : qos
    # example: { "topic1": 0, "topcic2" : 1 }
    topics = userdata
    for topic in topics.keys():
        logging.debug(f"subscribing to: [{topic}], qos={topics[topic]}")
        client.subscribe(topic, topics[topic])


# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
    logging.debug(f"message arrived: topic: {msg.topic} content: {str(msg.payload)}")


class MClient:
    def __init__(self, server_addr, client_id=None, topics=None):
        self.server_addr = server_addr
        if not client_id:
            self.client_id = f"mclient-{random.randint(0,1e8-1):08d}"
        else:
            self.client_id = client_id

        if not topics:
            self.topics = { }
        else:
            self.topic = topics

        self.client = mqtt.Client(client_id=self.client_id, userdata=self.topics)
        self.client.on_connect = on_connect
        self.client.on_message = on_message

        self.client.connect(server_addr, 1883, 60)

    def loop(self, timeout=0):
        if timeout == 0:
            self.client.loop_forever()
        else:
            self.client.loop(timeout=timeout)

    def publish(self, topic, payload, qos, retain=False):
        logging.debug(f"publish: topic: {topic} content: {get_hex_str(payload)}")
        self.client.publish(topic=topic, payload=payload, qos=qos, retain=retain)

    def publish_single(self, topic, payload, qos, retain):
        logging.debug(f"publish single: topic: {topic} -- content: {get_hex_str(payload)}")
        paho.mqtt.publish.single(topic, payload=payload, qos=qos, retain=retain,
            hostname=self.server_addr, client_id=self.client_id)

