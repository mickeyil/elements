#include <cassert>
#include <cstdio>
#include <cstring>

#include "element_topics.h"
#include "publisher.h"
#include "sensor.h"
#include "utils.h"


Sensor::Sensor()
{ 
  init();
}


void Sensor::init()
{
  _val_as_int = 0;
  _last_ts = 0;
  _publish_raw_counter = 1;
  bzero(&_sensor_params, sizeof(sensor_params_t));
  bzero(_publish_raw_topic, PUBLISH_RAW_TOPIC_LEN);
}


void Sensor::setup(void *params, unsigned int size, handlers_t& handlers)
{
  assert(header_size() == size);
  init();
  memcpy(&_sensor_params, params, sizeof(sensor_params_t));
  
  // prepare topic string if needed
  if (_sensor_params.publish_raw > 0) {
    const char *base_topic = handlers.ptopics->get_full_topic("sensors");
    snprintf(_publish_raw_topic, PUBLISH_RAW_TOPIC_LEN, "%s/%d/raw",
      base_topic, _sensor_params.id);
  }

  DPRINTF("Sensor::setup(): params=%p size=%u", params, size);

  // call specific subclass setup
  _setup((uint8_t*) params + sizeof(sensor_params_t), size-sizeof(sensor_params_t));
}


void Sensor::process(unsigned long t_now_ms, handlers_t& handlers)
{
  if (t_now_ms - _last_ts < _sensor_params.sample_inverval_ms) {
    return;
  }

  _last_ts = t_now_ms;

  // process sensor reading by specific class
  _process(handlers);

  if (_sensor_params.publish_raw > 0) {
    _publish_raw_counter--;
    if (_publish_raw_counter == 0) {
      _publish_raw_counter = _sensor_params.publish_raw;
      
      // call specific publish_raw to get pointer and size of payload
      const char *raw_cstr = _publish_raw();
      
      if (handlers.publisher != nullptr) {
        handlers.publisher->publish(_publish_raw_topic, raw_cstr);
      }
    }
  }
}
