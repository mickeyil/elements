#include "pixel_view.h"

#include <new>

PixelView::~PixelView()
{
    reset();
}

bool PixelView::initialize(
    hsva_t* buffer,
    uint16_t size,
    const uint16_t* storage_indices,
    bool has_physical_mapping,
    const uint16_t* physical_indices,
    bool physical_identity
)
{
    reset();

    if (buffer == nullptr && size > 0) {
        return false;
    }

    _buffer = buffer;
    _size = size;
    _has_physical_mapping = has_physical_mapping;
    _physical_identity = has_physical_mapping && physical_identity;

    if (storage_indices != nullptr) {
        _storage_indices = new (std::nothrow) uint16_t[size];
        if (_storage_indices == nullptr) {
            reset();
            return false;
        }

        for (uint16_t i = 0; i < size; i++) {
            _storage_indices[i] = storage_indices[i];
        }
    }

    if (has_physical_mapping && !physical_identity) {
        if (physical_indices == nullptr) {
            reset();
            return false;
        }

        _physical_indices = new (std::nothrow) uint16_t[size];
        if (_physical_indices == nullptr) {
            reset();
            return false;
        }

        for (uint16_t i = 0; i < size; i++) {
            _physical_indices[i] = physical_indices[i];
        }
    }

    return true;
}

void PixelView::reset()
{
    delete[] _storage_indices;
    delete[] _physical_indices;

    _storage_indices = nullptr;
    _physical_indices = nullptr;
    _buffer = nullptr;
    _size = 0;
    _has_physical_mapping = false;
    _physical_identity = false;
}

void PixelView::clear()
{
    for (uint16_t i = 0; i < _size; i++) {
        (*this)[i] = hsva_t();
    }
}
