#include "pixel_views.h"

#include <new>

PixelViews::~PixelViews()
{
    reset();
}

bool PixelViews::initialize(PixelBufferPool& buffers, const PixelViewSpec* specs, uint16_t count)
{
    reset();

    if (count == 0) {
        return true;
    }
    if (specs == nullptr) {
        return false;
    }

    _views = new (std::nothrow) PixelView[count];
    if (_views == nullptr) {
        return false;
    }
    _count = count;

    for (uint16_t i = 0; i < count; i++) {
        const PixelViewSpec& spec = specs[i];
        hsva_t* backing = buffers.buffer_at(spec.buffer_idx);
        if (backing == nullptr) {
            reset();
            return false;
        }

        if (!_views[i].initialize(
                backing,
                spec.size,
                spec.is_identity ? nullptr : spec.indices)) {
            reset();
            return false;
        }
    }

    return true;
}

void PixelViews::reset()
{
    delete[] _views;
    _views = nullptr;
    _count = 0;
}

uint16_t PixelViews::count() const
{
    return _count;
}

PixelView& PixelViews::at(uint16_t idx)
{
    return _views[idx];
}

const PixelView& PixelViews::at(uint16_t idx) const
{
    return _views[idx];
}
