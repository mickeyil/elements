#include "layer.h"

Layer::Layer(uint8_t id, const uint8_t* index_map, uint8_t length, uint8_t priority)
    : _id(id), _priority(priority), _length(length), _animation(nullptr)
{
    _index_map = new uint8_t[length];
    memcpy(_index_map, index_map, length);

    _buffer = new hsva_t[length];
    clear_buffer();
}

Layer::~Layer()
{
    delete[] _index_map;
    delete[] _buffer;
}
