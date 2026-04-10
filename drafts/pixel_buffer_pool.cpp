#include "pixel_buffer_pool.h"

#include <cstring>
#include <new>

PixelBufferPool::~PixelBufferPool()
{
    reset();
}

bool PixelBufferPool::initialize(const uint8_t* buffer_sizes, uint16_t buffer_count)
{
    reset();

    if (buffer_count == 0) {
        return true;
    }
    if (buffer_sizes == nullptr) {
        return false;
    }

    _buffers = new (std::nothrow) hsva_t*[buffer_count]();
    _sizes = new (std::nothrow) uint8_t[buffer_count];
    if (_buffers == nullptr || _sizes == nullptr) {
        reset();
        return false;
    }

    _buffer_count = buffer_count;

#ifdef ARDUINO
    uint32_t total_pixels = 0;
    for (uint16_t i = 0; i < buffer_count; i++) {
        total_pixels += buffer_sizes[i];
    }

    _pool = new (std::nothrow) hsva_t[total_pixels]();
    if (_pool == nullptr) {
        reset();
        return false;
    }

    _pool_size = total_pixels;

    uint32_t offset = 0;
    for (uint16_t i = 0; i < buffer_count; i++) {
        _buffers[i] = _pool + offset;
        _sizes[i] = buffer_sizes[i];
        offset += buffer_sizes[i];
    }
#else
    for (uint16_t i = 0; i < buffer_count; i++) {
        _sizes[i] = buffer_sizes[i];
        _buffers[i] = new (std::nothrow) hsva_t[buffer_sizes[i]]();
        if (_buffers[i] == nullptr && buffer_sizes[i] != 0) {
            reset();
            return false;
        }
    }
#endif

    return true;
}

void PixelBufferPool::reset()
{
#ifdef ARDUINO
    delete[] _pool;
    _pool = nullptr;
    _pool_size = 0;
#else
    if (_buffers != nullptr) {
        for (uint16_t i = 0; i < _buffer_count; i++) {
            delete[] _buffers[i];
        }
    }
#endif

    delete[] _buffers;
    delete[] _sizes;

    _buffer_count = 0;
    _buffers = nullptr;
    _sizes = nullptr;
}

uint16_t PixelBufferPool::buffer_count() const
{
    return _buffer_count;
}

hsva_t* PixelBufferPool::buffer_at(uint16_t buffer_idx)
{
    if (buffer_idx >= _buffer_count || _buffers == nullptr) {
        return nullptr;
    }
    return _buffers[buffer_idx];
}

const hsva_t* PixelBufferPool::buffer_at(uint16_t buffer_idx) const
{
    if (buffer_idx >= _buffer_count || _buffers == nullptr) {
        return nullptr;
    }
    return _buffers[buffer_idx];
}

uint8_t PixelBufferPool::buffer_size(uint16_t buffer_idx) const
{
    if (buffer_idx >= _buffer_count || _sizes == nullptr) {
        return 0;
    }
    return _sizes[buffer_idx];
}
