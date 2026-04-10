#include "pixel_view.h"

#include <new>

PixelView::~PixelView()
{
    reset();
}

bool PixelView::initialize(hsva_t* backing_buffer, uint8_t size, const uint16_t* indices)
{
    reset();

    _buffer = backing_buffer;
    _size = size;

    if (indices == nullptr) {
        return true;
    }

    _indices = new (std::nothrow) uint16_t[size];
    if (_indices == nullptr) {
        reset();
        return false;
    }

    for (uint8_t i = 0; i < size; i++) {
        _indices[i] = indices[i];
    }

    return true;
}

void PixelView::reset()
{
    delete[] _indices;
    _indices = nullptr;
    _buffer = nullptr;
    _size = 0;
}

uint8_t PixelView::size() const
{
    return _size;
}

bool PixelView::is_identity() const
{
    return _indices == nullptr;
}

bool PixelView::empty() const
{
    return _size == 0;
}

hsva_t& PixelView::operator[](uint8_t i)
{
    return _buffer[_indices ? _indices[i] : i];
}

const hsva_t& PixelView::operator[](uint8_t i) const
{
    return _buffer[_indices ? _indices[i] : i];
}

void PixelView::clear()
{
    for (uint8_t i = 0; i < _size; i++) {
        (*this)[i] = hsva_t();
    }
}
