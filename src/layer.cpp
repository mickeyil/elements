#include "layer.h"

Layer::~Layer()
{
    reset();
}

void Layer::initialize(AnimationEvent* events, uint16_t count)
{
    reset();
    _events = events;
    _count = count;
}

void Layer::reset()
{
    if (_events != nullptr) {
        for (uint16_t i = 0; i < _count; i++) {
            delete _events[i].animation;
        }
        delete[] _events;
    }
    _events = nullptr;
    _count = 0;
}

const AnimationEvent* Layer::active_at(float t) const
{
    for (uint16_t i = 0; i < _count; i++) {
        const AnimationEvent& e = _events[i];
        if (t < e.start) {
            // Events are sorted by start; nothing later can cover an earlier t.
            return nullptr;
        }
        if (t < e.start + e.duration) {
            return &e;
        }
    }
    return nullptr;
}
