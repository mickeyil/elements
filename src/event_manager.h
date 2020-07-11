#pragma once

#define MAX_EVENTS 10

#include "handlers.h"
#include "event.h"

class EventManager
{
  public:
    EventManager(handlers_t *phandlers);
    ~EventManager();
    
    void add_event(void* setup_data_buf, unsigned int size, const char **errstr);

    void process_events(double t_abs_now);

  private:

    Event* _events[MAX_EVENTS];
    unsigned int _size;
    handlers_t *_phandlers;
};