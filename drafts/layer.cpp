#include "layer.h"

Layer::~Layer()
{
    reset();
}

void Layer::initialize(AnimationEvent* events_, uint16_t event_count_)
{
    reset();

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

    events = nullptr;
    event_count = 0;
}
