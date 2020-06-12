#!/usr/bin/env python3
import json
import logging
import re
import signal
import sys
import time

import paho.mqtt.client as mqtt

import lib.logconfig
lib.logconfig.config_default_logger(level=logging.DEBUG)

logging.info('Control unit started.')

elements_seen = {}
element_status_pattern = re.compile("^elements/([A-Za-z0-9-._]+)/status$")

# Handle Ctrl-C
def signal_handler(sig, frame):
    logging.info(f"Stopping control unit.")
    client.disconnect()
    sys.exit(0)


def on_connect(client, userdata, flags, rc):
    logging.info("Connected to mqtt server.")
    client.subscribe("elements/+/status")


def on_message(client, userdata, msg):
    global report_change
    #logging.debug(f'on_message: {msg.topic} | {msg.payload}')
    match = element_status_pattern.match(msg.topic)
    if match:
        device_id = match.group(1)
        try:
            status = json.loads(msg.payload)
            is_connected = status['connected']
            elements_seen[device_id] = is_connected
            conn_str = 'online' if is_connected else 'offline'
            logging.info(f'device {device_id} went {conn_str}')
            report_change = True
        except Exception as e:
            logging.error(f'payload format error: {str(e)}')


signal.signal(signal.SIGINT, signal_handler)

client = mqtt.Client(client_id="controller")
client.on_connect = on_connect
client.connect('192.168.5.5', 1883, 60)
client.on_message = on_message

last_ts = time.time()
report_change = True
while True:
    client.loop()
    now_ts = time.time()
    if now_ts - last_ts > 60.0 or report_change:
        last_ts = now_ts
        report_change = False
        elements_online = [e for e in elements_seen if elements_seen[e]]
        logging.debug(f'connected devices: {", ".join(elements_online)}')
