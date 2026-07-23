#include "core/pixel_views.h"

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
        hsva_t* buffer = buffers.buffer_at(spec.buffer_idx);
        if (buffer == nullptr) {
            reset();
            return false;
        }
        if (spec.storage_identity && spec.size > buffers.buffer_size(spec.buffer_idx)) {
            reset();
            return false;
        }
        if (!spec.storage_identity && spec.storage_indices == nullptr) {
            reset();
            return false;
        }
        // Identity flags must not also carry an index array.
        if (spec.storage_identity && spec.storage_indices != nullptr) {
            reset();
            return false;
        }
        if (spec.physical_identity && spec.physical_indices != nullptr) {
            reset();
            return false;
        }
        // physical_identity only makes sense when has_physical_mapping is set.
        if (!spec.has_physical_mapping && spec.physical_identity) {
            reset();
            return false;
        }
        if (spec.has_physical_mapping && !spec.physical_identity
            && spec.physical_indices == nullptr) {
            reset();
            return false;
        }

        if (!_views[i].initialize(
                buffer,
                spec.size,
                spec.storage_identity ? nullptr : spec.storage_indices,
                spec.has_physical_mapping,
                spec.physical_identity ? nullptr : spec.physical_indices,
                spec.physical_identity)) {
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
