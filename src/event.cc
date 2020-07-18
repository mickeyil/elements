#include <cassert>
#include <cstdio>
#include <cstring>

#include "event.h"
#include "element_topics.h"
#include "sensor_manager.h"
#include "sensor.h"
#include "utils.h"

#include "publisher.h"


void Event::setup(const void *params, unsigned int size, handlers_t& handlers)
{
  memcpy(&_event_params, params, sizeof(event_params_t));
  DPRINTF("size=%u  --  event_params_t=%zu report_topic_len=%u sub_header_size=%u",
    size, sizeof(event_type_t), _event_params.report_topic_len, sub_header_size());
  assert(size == sizeof(event_params_t) + _event_params.report_topic_len + sub_header_size());

  const char * p_topic = static_cast<const char *>(params) + sizeof(event_params_t);
  
  snprintf(_report_topic, EVENT_MAX_TOPIC_LEN, "%s%s/%d/%d", handlers.ptopics->get_prefix(),
    "events", _event_params.event_id, _event_params.sensor_id);
  
  if (_p_samping_window == nullptr) {
    _p_samping_window = new SamplingWindow<int16_t>(_event_params.sampling_window_size, 0);
  } else {
    if (_event_params.sampling_window_size != _p_samping_window->size()) {
      DPRINTF("sampling window resize: %u -> %u", _p_samping_window->size(),
        _event_params.sampling_window_size);
      delete _p_samping_window;
      _p_samping_window = new SamplingWindow<int16_t>(_event_params.sampling_window_size, 0);
    }
  }

  DPRINTF("Event::setup(): params=%p size=%u", params, size);

  // call specific subclass setup
  _setup(p_topic + _event_params.report_topic_len, 
    size - sizeof(event_params_t) - _event_params.report_topic_len);
}


void Event::sample_sensor(handlers_t& handlers)
{
  unsigned int id = _event_params.sensor_id;
  SensorManager *psm = handlers.psensor_mgr;
  Sensor *sensor = psm->get_sensor(id);
  assert(sensor != nullptr);  // FIXME

  // sample data only if there's new data to read, i.e. sensor process() did some work.
  if (sensor->get_last_ts() != _last_sample_time) {
    _last_sample_time = sensor->get_last_ts();
    _p_samping_window->sample(sensor->val_as_int());
  }
}


void Event::process(double t_abs_now, handlers_t& handlers)
{
  bool cond = _condition(_p_samping_window);
  if (_event_params.polarity == 0) {
    cond = !cond;
  }
  
  if (cond == true) {
    //DPRINTF("event %s occured at %lf. reporting to topic: %s", name(), t_abs_now,
    //  _report_topic);
    
    #if 0
    printf(">> sampling window: ");
    for (unsigned int i = 0; i < _p_samping_window->size(); i++) {
      printf("%d ", _p_samping_window->get_item(i));
    }
    printf("\n");
    fflush(stdout);
    #endif

    if (t_abs_now - _last_event_time < _event_params.supress_time) {
      //DPRINTF("-- suppressed (%lf past).", t_abs_now - _last_event_time);
      return;
    }
    _last_event_time = t_abs_now;
    
    // write event id as a string to the msg buffer
    snprintf(_msgbuf, EVENT_MAX_MSG_BUF, "{\"time\":%lf}", t_abs_now);
    
    // publish the event
    handlers.publisher->publish(_report_topic, _msgbuf);
  }
}
