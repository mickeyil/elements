#include <cassert>
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
  assert(size == sizeof(event_params_t) + _event_params.report_topic_len + sub_header_size());

  const char * p_topic = static_cast<const char *>(params) + sizeof(event_params_t);
  
  std::string stopic(p_topic, p_topic + _event_params.report_topic_len);
  _report_topic = std::string(handlers.ptopics->get_full_topic(stopic.c_str()));
  
  if (_event_params.sampling_window_size > 1) {
    _p_samping_window = new SamplingWindow<int16_t>(_event_params.sampling_window_size, 0);
  }

  DPRINTF("Event::setup(): params=%p size=%u", params, size);

  // call specific subclass setup
  _setup(p_topic + _event_params.report_topic_len, 
    size - sizeof(event_params_t) - _event_params.report_topic_len);
}


void Event::sample_sensor(handlers_t& handlers)
{
  unsigned int idx = _event_params.sensor_id;
  SensorManager *psm = handlers.psensor_mgr;
  Sensor *sensor = psm->get_sensor(idx);
  assert(sensor != nullptr);  // FIXME
  _p_samping_window->sample(sensor->val_as_int());
}


void Event::process(double t_abs_now, handlers_t& handlers)
{
  bool cond = _condition(_p_samping_window);
  if (_event_params.polarity == 0) {
    cond = !cond;
  }
  
  if (cond == true) {
    if (t_abs_now - _last_event_time < _event_params.supress_time) {
      return;
    }
    _last_event_time = t_abs_now;
    DPRINTF("event %s occured at %lf. reporting to topic: %s", name(), t_abs_now,
      _report_topic.c_str());
    
    // write event id as a string to the msg buffer
    snprintf(_msgbuf, EVENT_MAX_MSG_BUF, "%d", _event_params.event_id);
    
    // publish the event
    handlers.publisher->publish(_report_topic.c_str(), _msgbuf);
  }
}
