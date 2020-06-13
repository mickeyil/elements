/*
 * Requirements:
 *   PubSubClient by Nick O'Leary (>= 2.7.0)
 */

#include <ESP8266WiFi.h>
#include <ESP8266mDNS.h>

#include <PubSubClient.h>

// route requests for device by topic
#include "routereq.h"

#include "sensors.h"

// This file contains the #define statements for SECRET_* constants and should
// not be uploaded to GitHub / src code repo.
#include "secrets.h"

// WiFi credentials
// The SSID (name) of the Wi-Fi network you want to connect to
const char* ssid = SECRET_WIFI_SSID;

// The password of the Wi-Fi network
const char* password = SECRET_WIFI_PASSWORD;

// MQTT server address
const char* mqtt_server = "192.168.5.5";

// setup an instance of MQTT client
WiFiClient espClient;
PubSubClient client(espClient);

// define payload message buffer
#define MSG_BUFFER_SIZE  512
char payload_msg[MSG_BUFFER_SIZE];

// topic for publishing status
String topic_status;

// topic pattern to subscribe to
String topic_operate;

#define MAX_MQTT_CONNECTION_ATTEMPTS 12


// sensor data saved here. when sensor type is determined via an operate command
// this pointer should be allocated.
sensor_data_t sensor_data;

// helper for loop invocation measurements
unsigned int loop_ticks = 0;

// contains a chipid in the format of ESP-XXXXXXXX with X's being 32 bits
// of id unique to the ESP chip in hex numbers.
char chipid[16];

unsigned long last_ts = 0;

void setup() {

  Serial.begin(115200); // Start the Serial communication to send messages to the computer delay(10);
  delay(10);
  Serial.println("\r\n");

  startWifi();

  // get and print chip id
  uint32_t chipidnum = ESP.getChipId();
  snprintf(chipid, 16, "ESP-%08X", chipidnum);
  Serial.print("Chip ID: ");
  Serial.println(chipid);

  topic_status = String("elements/") + String(chipid) + String("/status");
  topic_operate = String("elements/") + String(chipid) + String("/operate/#");

  // Setup MQTT Server
  client.setServer(mqtt_server, 1883);

  // set callback for message arrival
  client.setCallback(callback);

  // connect to mqtt server
  mqtt_connect();

  // FastLED Related
  FastLED.addLeds<NEOPIXEL, 3>(rgb_array, RGB_ARRAY_SIZE);

  // sensor related
  // If this device is a sensor sending periodic readouts, type is set.
  sensor_type = SENSOR_NOT_AVAILABLE;
}

void loop()
{
  if (!client.connected()) {
    mqtt_connect();
  }

  client.loop();

  unsigned long now = millis();
  loop_ticks += 1;

  if (now - last_ts > 5000) {
    last_ts = now;
    Serial.print("\n[");
    Serial.print(loop_ticks);
    Serial.println("] ");
    loop_ticks = 0;
  }

  if (sensor_type != SENSOR_NOT_AVAILABLE) {
    process_sensor(sensor_type, &sensor_data, now);
  }
}


void startWifi()
{
  // connect to wifi
  WiFi.begin(ssid, password);
  Serial.print("Connecting to ");
  Serial.print(ssid);
  Serial.println(" ...");
  
  int i = 0;
  while (WiFi.status() != WL_CONNECTED) { // Wait for the Wi-Fi to connect
    delay(1000);
    Serial.print(++i); Serial.print(' '); 
  }
  Serial.println('\n');
  Serial.println("Connection established!");
  Serial.print("IP address:\t");
  Serial.println(WiFi.localIP()); // Send the IP address of the ESP8266 to the computer
}


void mqtt_connect()
{
  int attempts = 1;
  while (!client.connected()) {
    Serial.print("Connecting to mqtt server: ");

    // connect to mqtt broker. set last will to be (retained):
    // topic: elements/<device_id>/status
    // payload: {"connected":false}
    if (client.connect(chipid, topic_status.c_str(), 1, true, "{\"connected\":false}")) {

      // connection established
      Serial.println("connected.");
      client.publish(topic_status.c_str(), "{\"connected\":true}", true);
      client.subscribe(topic_operate.c_str());
    } else {

      // connection attempt failed
      if (attempts >= MAX_MQTT_CONNECTION_ATTEMPTS) {
        ESP.restart();
      } else {
        attempts += 1;
        // Wait 5 seconds before retrying
        Serial.print("failed, rc=");
        Serial.print(client.state());
        Serial.println(" -- trying again in 5 seconds");
        delay(5000);
      }
    }
  }
}


void callback(char* topic, byte* payload, unsigned int length) {
  Serial.print("Message arrived [");
  Serial.print(topic);
  Serial.print("] size: ");
  Serial.println(length);

#if 0
  // debug printing of the payload data
  for (int i = 0; i < length; i++) {
    Serial.print((char)payload[i]);
  }
  Serial.println();
#endif
    
  routerequest(topic, payload, length);
}

