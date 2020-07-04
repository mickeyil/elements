#pragma once

#include <cstdint>
#include <cstdio>

#include "sensor.h"
#include "sample.h"


typedef struct __attribute__((__packed__)) {
  uint8_t trigger_pindnum;    // pin connected to trigger
  uint8_t echo_pindnum;       // pin connected to echo   
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
























// #define DIST_MAX_SAMPLE_WINDOW   20

//  uint8_t median_window;  // median sample window size. range: [1, DIST_MAX_SAMPLE_WINDOW]
//    Sample<uint16_t, DIST_MAX_SAMPLE_WINDOW> _samples;    // raw readout data

#if 0
// approached event describes a transition from being out of range (min,max)
// to being inside of range
typedef struct __attribute__((__packed__)) {
  uint16_t range_min_dist_cm;
  uint16_t range_max_dist_cm;
  float confidence;     // minimum percent of "in range" samples in sample window
} event_in_range_t;
#endif
