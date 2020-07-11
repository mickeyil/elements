#pragma once

#include <cstdint>
#include "event.h"

typedef struct __attribute__((__packed__)) {
  uint16_t  min;    // range is defined as: [min,max] (inclusive)
  uint16_t  max;
  
  // confidence: value in [0,1]. minimum fraction of values in the sampling 
  // window with condition = true for the event to be conditioned true.
  float     confidence;
} range_params_t;


class EventRange : public Event
{
  public:
    virtual ~EventRange() { }

    virtual uint16_t sub_header_size() const
    {
      return sizeof(range_params_t);
    }
  
  protected:
    
    virtual void _setup(const void *params, unsigned int size);
    
    virtual bool _condition(SamplingWindow<int16_t> *p_sampling_window);

    virtual const char *name()
    {
      return "in_range";
    }

  private:
    range_params_t _range_params;

};
