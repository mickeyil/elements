#pragma once

#include <cstdint>
#include <cstdio>

#include "sensor.h"

#ifdef ARDUINO
#include <Arduino.h>
static uint32_t get_free_heap()
{
  return ESP.getFreeHeap();
}
#else
static uint32_t get_free_heap()
{
  return 9999999;
}
#endif

class SensorFreeHeap : public Sensor {
  
  public:
    SensorFreeHeap() { }
    virtual ~SensorFreeHeap() { }
    
    virtual uint32_t header_size() const {
      return sizeof(sensor_params_t);
    }
    
  protected:
    virtual void _setup(void *params, unsigned int size) { }

    virtual void _process(handlers_t& handlers)
    {
      _free_heap = get_free_heap();
      _val_as_int = static_cast<int16_t>(_free_heap);
    }

    virtual const char * _publish_raw()
    {
      snprintf(_readout_buf, sizeof(_readout_buf), "%d", _free_heap);
      return _readout_buf;
    }

  private:
    uint32_t _free_heap;    // current sensor readout, in cm
    char _readout_buf[8];
};

