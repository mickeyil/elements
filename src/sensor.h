#pragma once

#include "handlers.h"

#ifdef ARDUINO
#include <Arduino.h>
#else
// for simulation client
static const uint8_t D0   = 90;
static const uint8_t D1   = 91;
static const uint8_t D2   = 92;
static const uint8_t D3   = 93;
static const uint8_t D4   = 94;
static const uint8_t D5   = 95;
static const uint8_t D6   = 96;
static const uint8_t D7   = 97;
static const uint8_t D8   = 98;
static const uint8_t D9   = 99;
static const uint8_t D10  = 100;
#endif

static const char* strpin_map[11] = {
  "D0", "D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "D9", "D10", 
};

#define PUBLISH_RAW_TOPIC_LEN 64

typedef enum {
    SENSOR_TYPE_BUTTON = 1000,
    SENSOR_TYPE_DISTANCE,
} sensor_type_t;


typedef struct __attribute__((__packed__)) {
    uint16_t id;    // sensor id to allow more than one on the same device
    uint16_t min_interval_ms;   // min interval between two process() calls in milliseconds.

    // non zero publish_raw will cause a publish once in a #publish_raw times of
    // the raw sensor reading to elemenes/<chip_id>/sensors/<id>/raw
    // example: publish_raw = 10 and min_interval_ms = 100 will cause a publish
    //          of raw reading every 1 second.
    uint8_t  publish_raw;
} sensor_params_t;


class Sensor
{
  public:

    Sensor();
    virtual ~Sensor() { }

    // sensor setup
    void setup(void *params, unsigned int size, handlers_t& handlers);

    // process sensor. a *quick* readout/processing of a sensor reading.
    // handlers_t is passed here so if a message is needed to be published,
    // it's done within this function.
    void process(unsigned long t_now_ms, handlers_t& handlers);
  
  protected:

    // specific sensor setup function, called by setup()
    virtual void _setup(void *params, unsigned int size) = 0;

    // specific sensor process function, called by process()
    virtual void _process(handlers_t& handlers) = 0;

    // default implementation: nothing to publish
    virtual const char * _publish_raw() 
    { 
      return "";
    };

  private:

    sensor_params_t _sensor_params;
    unsigned long _last_ts;   // last timestamp, in milliseconds
    char _publish_raw_topic[PUBLISH_RAW_TOPIC_LEN];
    unsigned int _publish_raw_counter;
};
