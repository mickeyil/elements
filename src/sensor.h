#pragma once
#include <cstdint>

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

// dev_pin_map array is used for translating D<N> pins to their D defined value.
// i.e.: pin with value 8 will get the value set by D8 #define in Arduino.h.
static const uint8_t dev_pin_map[11] = {
  D0, D1, D2, D3, D4, D5, D6, D7, D8, D9, D10 
};

static const char* dev_pin_map_str[11] = {
  "D0", "D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "D9", "D10", 
};

#define SENSOR_UNINITIALIZED_PIN  99

#define PUBLISH_RAW_TOPIC_LEN 64

typedef enum {
    SENSOR_TYPE_DISTANCE = 0,
    SENSOR_TYPE_BUTTON,
    SENSOR_TYPE_FREEHEAP,
} sensor_type_t;

static const char *sensor_types_str[] = {
  "distance HC-SR04", "button", "free heap"
};

typedef struct __attribute__((__packed__)) {
    uint16_t sensor_type;
    uint16_t id;    // sensor id enables using more than one sensor on the same device
    uint16_t sample_inverval_ms;   // min interval between two process() calls in milliseconds.

    // non zero publish_raw will cause a publish once in a #publish_raw times of
    // the raw sensor reading to elemenes/<chip_id>/sensors/<id>/raw
    // example: publish_raw = 10 and min_interval_ms = 100 will cause a publish
    //          of raw reading every 1 second.
    uint16_t  publish_raw;

} sensor_params_t;


class Sensor
{
  public:

    Sensor();
    
    // initialize sensor object
    void init();
    virtual ~Sensor() { }

    // sensor setup
    void setup(void *params, unsigned int size, handlers_t& handlers);

    // process sensor. a *quick* readout/processing of a sensor reading.
    // handlers_t is passed here so if a message is needed to be published,
    // it's done within this function.
    void process(unsigned long t_now_ms, handlers_t& handlers);
  
    virtual uint32_t header_size() const = 0;

    unsigned int id() const { return _sensor_params.id; }
    
    const char * typestr() const 
    { 
      return sensor_types_str[(uint16_t) _sensor_params.sensor_type]; 
    }
    
    int16_t val_as_int() const { return _val_as_int; }
    
    // last sensor processing timestamp
    unsigned long get_last_ts() const { return _last_ts; }

  protected:
    int16_t _val_as_int;

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
