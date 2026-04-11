#include "layer.h"

Layer::~Layer()
{
    reset();
}

void Layer::initialize(
    hsva_t* buffer_,
    uint16_t buffer_length_,
    uint16_t* physical_map_,
    AnimationEvent* events_,
    uint16_t event_count_
)
{
    reset();

    buffer = buffer_;
    buffer_length = buffer_length_;
    physical_map = physical_map_;
    event_count = event_count_;
    events = events_;
}

void Layer::reset()
{
    if (events != nullptr) {
        for (uint16_t i = 0; i < event_count; i++) {
            delete events[i].animation;
        }
    }

    delete[] events;
    delete[] physical_map;

    events = nullptr;
    physical_map = nullptr;
    event_count = 0;
    buffer = nullptr;
    buffer_length = 0;
}
