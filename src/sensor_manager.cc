#include <cassert>
#include <cstring>

#include "sensor_manager.h"
#include "sensor.h"
#include "sensors/distance.h"

#include "utils.h"


static Sensor* create_sensor(sensor_type_t sensor_type)
{
  Sensor *sensor = nullptr;

  switch(sensor_type) {
    case SENSOR_TYPE_DISTANCE:
      sensor = new SensorDistance;
      break;
    
    default:
      return nullptr;
  }
  
  assert(sensor != nullptr);    // out of memory is fatal
  return sensor;
}


SensorManager::SensorManager(handlers_t *phandlers) : _size(0), _phandlers(phandlers)
{
  for (unsigned int i = 0; i < MAX_SENSORS; i++) {
    _sensors[i] = nullptr;
  }
}


SensorManager::~SensorManager()
{
  for (unsigned int i = 0; i < _size; i++) {
    assert(_sensors[i] != nullptr);
    delete _sensors[i];
  }
}


void SensorManager::add_sensor(void* setup_data_buf, unsigned int size, const char **errstr)
{
  if (_size == MAX_SENSORS) {
    *errstr = "add_sensor(): sensors array at full capacity";
    return;
  }

  sensor_params_t sensor_params;
  memcpy(&sensor_params, setup_data_buf, sizeof(sensor_params_t));

  // verify there's not already a sensor with the same id
  for (unsigned int i = 0; i < _size; i++) {
    if (_sensors[i]->id() == sensor_params.id) {
      *errstr = "sensor id exists";
      return;
    }
  }

  Sensor * sensor = create_sensor(static_cast<sensor_type_t>(sensor_params.sensor_type));
  if (sensor == nullptr) {
    *errstr = "create_sensor() failed: invalid sensor type";
    return;
  }

  _sensors[_size] = sensor;
  _size++;

  sensor->setup(setup_data_buf, size, *_phandlers);
  DPRINTF("add_sensor(): added sensor id: %u, type: %s", sensor->id(), sensor->typestr());
}


void SensorManager::process_sensors(unsigned long t_now_ms)
{
  for (unsigned int i = 0; i < _size; i++) {
    assert(_sensors[i] != nullptr);
    _sensors[i]->process(t_now_ms, *_phandlers);
  }
}
