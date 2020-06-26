#include <ESP8266WiFi.h>
#include <ESP8266mDNS.h>
#include <WiFiUdp.h>
#include <NTPClient.h>

#include <PubSubClient.h>

#define FASTLED_ESP8266_DMA
#include "FastLED/FastLED.h"

// route requests for device by topic
#include "requests.h"

//#include "sensors.h"

// This file contains the #define statements for SECRET_* constants and should
// not be uploaded to GitHub / src code repo.
#include "secrets.h"

// pointers to various data structures is propagated to handler functions using 
// handler_t struct
#include "handlers.h"
#include "element_topics.h"
#include "syncedtime.h"

handlers_t handlers;

// WiFi credentials
// The SSID (name) of the Wi-Fi network you want to connect to
const char* ssid = SECRET_WIFI_SSID;

// The password of the Wi-Fi network
const char* password = SECRET_WIFI_PASSWORD;

// MQTT server address
const char* mqtt_server = "192.168.5.5";
const char* ntp_server = mqtt_server;
const long utcOffsetInSeconds = 7200;

// setup an instance of MQTT client
WiFiClient espClient;
PubSubClient client(espClient);

WiFiUDP ntpUDP;
NTPClient timeClient(ntpUDP, ntp_server, utcOffsetInSeconds, 2000);
SyncedTime *psynced_time = nullptr; // (&timeClient);

// define payload message buffer
#define MSG_BUFFER_SIZE  512
char payload_msg[MSG_BUFFER_SIZE];

// topic for publishing status
String topic_status;

// topic pattern to subscribe to
String topic_operate;

// topic for publishing distance sensor reading
String topic_distance;

#define MAX_MQTT_CONNECTION_ATTEMPTS 12

// Element topics is a helper for getting full mqtt topics in elements formatting.
ElementTopics topics;


// helper for loop invocation measurements
unsigned int loop_ticks = 0;

// contains a chipid in the format of ESP-XXXXXXXX with X's being 32 bits
// of id unique to the ESP chip in hex numbers.
char chipid[16];

unsigned long last_ts = 0;


// LEDs related
// define CRGB array - FastLED's r,g,b (u8) array for writing signal to LED strip
#define RGB_ARRAY_SIZE        150
CRGB rgb_array[RGB_ARRAY_SIZE];

// Animation Manager operates directly on the RGB buffer and manages all animation
// requests.
AnimationManager anim_mgr((uint8_t*) rgb_array, RGB_ARRAY_SIZE);


void setup() {

  Serial.begin(115200); // Start the Serial communication to send messages to the computer delay(10);
  delay(10);
  Serial.println("\r\n");

  startWifi();

  // get and print chip id
  uint32_t chipidnum = ESP.getChipId();
  snprintf(chipid, 16, "ESP-%08X", chipidnum);
  topics.set_chipid(chipid);
  handlers.ptopics = &topics;
  
  Serial.print("Chip ID: ");
  Serial.println(chipid);

  //topic_distance = String("elements/") + String(chipid) + String("/sensors/distance");

  // Setup MQTT Server
  client.setServer(mqtt_server, 1883);

  // set callback for message arrival
  client.setCallback(callback);

  // connect to mqtt server
  mqtt_connect();

  // start ntp client
  timeClient.begin();
  psynced_time = new SyncedTime(&timeClient);
  // synced_time.sync();

  // FastLED Related
  FastLED.addLeds<NEOPIXEL, 3>(rgb_array, RGB_ARRAY_SIZE);

  // Animation related
  handlers.panim_mgr = &anim_mgr;

  // sensor related
  // If this device is a sensor sending periodic readouts, type is set.
  // sensor_type = SENSOR_NOT_AVAILABLE;

}

void loop()
{
  if (!client.connected()) {
    mqtt_connect();
  }

  client.loop();

  unsigned long now = millis();
  loop_ticks += 1;

  if (now - last_ts > 300) {    
    last_ts = now;
    Serial.print("\n[");
    Serial.print(loop_ticks);
    Serial.print("] ");
    psynced_time->sync();
    double t_now_lf = psynced_time->get_time_lf();
    Serial.println(t_now_lf);
    loop_ticks = 0;
  }

  #if 0
  if (sensor_type != SENSOR_NOT_AVAILABLE) {
    process_sensor(sensor_type, client, now, topic_distance);
  }
  #endif

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
    const char *topic = handlers.ptopics->get_full_topic("status");
    if (client.connect(chipid, topic, 1, true, "{\"connected\":false}")) {

      // connection established
      Serial.println("connected.");
      client.publish(topic, "{\"connected\":true}", true);
      
      // subscribe to operate topics
      topic = handlers.ptopics->get_full_topic("operate/#"); 
      Serial.print("subscribe to: ");
      Serial.println(topic);
      client.subscribe(topic);
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
  unsigned long t_s = millis();
  Serial.print("Message arrived [");
  Serial.print(topic);
  Serial.print("] size: ");
  Serial.println(length);
  return;
#if 0
  // debug printing of the payload data
  for (int i = 0; i < length; i++) {
    Serial.print((char)payload[i]);
  }
  Serial.println();
#endif
  #if 0
  timeClient.update();
  Serial.print("tc milli: ");
  Serial.print(timeClient.getEpochMSec());
  Serial.print("  millis(): ");
  Serial.println(millis());
  double t_now = static_cast<double>(timeClient.getEpochTime()) 
               + static_cast<double>(timeClient.getEpochMSec()) / 1000.0;
  #endif
  double t_now = 11.0;
  // double t_now = synced_time.get_time_lf();
  Serial.print("ts: ");
  Serial.println(t_now);
  handlers.t_now = t_now;

  const char *errstr = nullptr;
  
  process_request(topic, payload, length, handlers, &errstr);
  if (errstr != nullptr) {
    Serial.print("error: ");
    Serial.println(errstr);
  } else {
    Serial.println("request processed successfully.");
  }
  unsigned long t_e = millis();
  Serial.print("callback process time [ms]: ");
  Serial.println(t_e-t_s);
}


