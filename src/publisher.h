#pragma once
#include <cstring>

#ifdef ARDUINO
#include <PubSubClient.h>
#else
#include "mqtt/client.h"
#endif

class Publisher
{
  public:
  #ifdef ARDUINO
  Publisher(PubSubClient& client) : pclient(&client) { }
  #else
  Publisher(mqtt::client& client) : pclient(&client) { }
  #endif

  void publish(const char *topic, const char *payload_cstr) 
  {
  #ifdef ARDUINO
    pclient->publish(topic, payload_cstr);
  #else
    pclient->publish(topic, payload_cstr, strlen(payload_cstr)+1);
  #endif
  }

  private:
  #ifdef ARDUINO
  PubSubClient *pclient;
  #else
  mqtt::client *pclient;
  #endif
};
