#include <cassert>
#include <cstring>

#include "handlers.h"
#include "event_manager.h"
#include "event.h"
#include "events/range.h"

#include "utils.h"


static Event* create_event(event_type_t event_type)
{
  Event *event = nullptr;

  switch(event_type) {
    case EVENT_TYPE_IN_RANGE:
      event = new EventRange;
      break;
    
    default:
      return nullptr;
  }
  
  assert(event != nullptr);    // out of memory is fatal
  return event;
}


EventManager::EventManager(handlers_t *phandlers) : _size(0), _phandlers(phandlers)
{
  for (unsigned int i = 0; i < MAX_EVENTS; i++) {
    _events[i] = nullptr;
  }
}


EventManager::~EventManager()
{
  for (unsigned int i = 0; i < _size; i++) {
    assert(_events[i] != nullptr);
    delete _events[i];
  }
}


void EventManager::add_event(void* setup_data_buf, unsigned int size, const char **errstr)
{
  if (_size == MAX_EVENTS) {
    *errstr = "add_event(): events array at full capacity";
    return;
  }

  event_params_t event_params;
  memcpy(&event_params, setup_data_buf, sizeof(event_params_t));

  // verify there's not already a sensor with the same id
  Event* event = nullptr;
  for (unsigned int i = 0; i < _size; i++) {
    if (_events[i]->id() == event_params.event_id) {
      event = _events[i];
      DPRINTF("reinitializing event: %u", event->id());
      break;
    }
  }

  // no existing id was found, create a new instance
  if (event == nullptr) {
    event = create_event(static_cast<event_type_t>(event_params.event_type));
    if (event == nullptr) {
      *errstr = "create_sensor() failed: invalid sensor type";
      return;
    }
  }

  _events[_size] = event;
  _size++;

  event->setup(setup_data_buf, size, *_phandlers);
  DPRINTF("add_sensor(): added sensor id: %u", event->id());
}


void EventManager::process_events(double t_abs_now)
{
  for (unsigned int i = 0; i < _size; i++) {
    Event *event = _events[i];
    assert(event != nullptr);
    unsigned long sample_time = event->last_sample_time();
    event->sample_sensor(*_phandlers);
    
    // process event only if sampling window contains new data
    if (event->last_sample_time() != sample_time) {
      event->process(t_abs_now, *_phandlers);
    }
  }
}
