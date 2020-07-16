#pragma once

#define MAX_SENSORS 10

#include "handlers.h"
#include "sensor.h"

class SensorManager
{
  public:
    SensorManager(handlers_t *phandlers);
    ~SensorManager();
    
    void add_sensor(void* setup_data_buf, unsigned int size, const char **errstr);

    void process_sensors(unsigned long t_now_ms);

    Sensor* get_sensor(unsigned int id);
  private:

    Sensor* _sensors[MAX_SENSORS];
    unsigned int _size;
    handlers_t *_phandlers;
};