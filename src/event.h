#pragma once

#include <cstdint>
#include <string>
#include "handlers.h"
#include "sampling_window.h"


#define EVENT_MAX_MSG_BUF 64
#define EVENT_MAX_TOPIC_LEN 48

typedef enum {
  EVENT_TYPE_IN_RANGE = 0,
} event_type_t;


typedef struct __attribute__((__packed__)) {
  uint16_t event_type;      // event type id: (int) event_type_t
  uint16_t  event_id;       // id for this event
  uint16_t  sensor_id;      // id of the sampled sensor
  float     supress_time;   // amount of time, in seconds, to ignore subsequent events

  // polarity: 1 means event happens when condition is true, 0 the opposite.
  uint8_t   polarity;

  // amount of recent samples to consider for noise reduction methods like
  // confidence
  uint8_t   sampling_window_size;
  
  // string naming the topic for reporting the event.
  // report is done to elements/<chip-id>/events/<report_topic>. payload
  // contains absolute time the event happened. Time is seconds since epoch in 
  // double percision floating point.
  uint8_t   report_topic_len;

} event_params_t;


class Event
{
  public:
    Event() : _p_samping_window(nullptr),
      _last_event_time(0.0), _last_sample_time(0)
      { }
    
    virtual ~Event() 
    { 
      if (_p_samping_window != nullptr) {
        delete _p_samping_window;
      } 
    }

    void setup(const void *params, unsigned int size, handlers_t& handlers);

    void sample_sensor(handlers_t& handlers);

    void process(double t_abs_now, handlers_t& handlers);

    virtual uint16_t sub_header_size() const = 0;

    unsigned int id() const { return _event_params.event_id; }

    unsigned long last_sample_time() const { return _last_sample_time; }

  protected:

    virtual void _setup(const void *params, unsigned int size) = 0;

    virtual bool _condition(SamplingWindow<int16_t> *p_sampling_window) = 0;

    virtual const char *name() = 0;

  private:
    event_params_t _event_params;
    char _report_topic[EVENT_MAX_TOPIC_LEN];
    SamplingWindow<int16_t> *_p_samping_window;
    double _last_event_time;
    char _msgbuf[EVENT_MAX_MSG_BUF];
    
    // last sample time in milliseconds format
    unsigned long _last_sample_time;
};