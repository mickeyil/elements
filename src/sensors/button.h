#pragma once

#include <cstdint>
#include <cstdio>

#include "sensor.h"

// button sensor
// for proper operation this needs a pull-down connection to a 10Kohm resistor
// on a ground leg.

typedef struct __attribute__((__packed__)) {
  uint8_t button_dpin;
} button_params_t;


class SensorButton : public Sensor {
  
  public:
    SensorButton() : _button_pin(SENSOR_UNINITIALIZED_PIN), 
                     _state(SENSOR_UNINITIALIZED_PIN) { }
    virtual ~SensorButton() { }
    
    virtual uint32_t header_size() const {
      return sizeof(sensor_params_t) + sizeof(button_params_t);
    }
    
  protected:
    virtual void _setup(void *params, unsigned int size);

    virtual void _process(handlers_t& handlers);

    virtual const char * _publish_raw()
    {
      snprintf(_readout_buf, sizeof(_readout_buf), "%d", _state);
      return _readout_buf;
    }

  private:
    button_params_t _params;
    uint8_t _button_pin;
    uint8_t _state;    // 1 for "pressed", 0 otherwise
    char _readout_buf[8];
};
