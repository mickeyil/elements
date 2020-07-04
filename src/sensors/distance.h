#pragma once

#include <cstdint>
#include <cstdio>

#include "sensor.h"
#include "sample.h"


typedef struct __attribute__((__packed__)) {
  uint8_t trigger_dpin;    // pin connected to trigger
  uint8_t echo_dpin;       // pin connected to echo   
} dist_params_t;


class SensorDistance : public Sensor {
  
  public:
    SensorDistance() : _trig_pin(0), _echo_pin(0) { }
    virtual ~SensorDistance() { }
    
    virtual uint32_t header_size() const {
      return sizeof(sensor_params_t) + sizeof(dist_params_t);
    }
    
  protected:
    virtual void _setup(void *params, unsigned int size);

    virtual void _process(handlers_t& handlers);

    virtual const char * _publish_raw()
    {
      snprintf(_readout_buf, sizeof(_readout_buf), "%d", _distance_cm);
      return _readout_buf;
    }

  private:
    dist_params_t _params;
    uint8_t _trig_pin;
    uint8_t _echo_pin;
    int _distance_cm;    // current sensor readout, in cm
    char _readout_buf[8];
};
