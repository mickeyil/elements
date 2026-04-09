#include "pixel_view.h"

PixelView::PixelView(hsva_t* backing_buffer, uint8_t size)
{
    bind(backing_buffer, nullptr, size);
}

PixelView::PixelView(hsva_t* backing_buffer, const uint16_t* indices, uint8_t size)
{
    bind(backing_buffer, indices, size);
}

void PixelView::bind(hsva_t* backing_buffer, const uint16_t* indices, uint8_t size)
{
    _buffer = backing_buffer;
    _indices = indices;
    _size = size;
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
