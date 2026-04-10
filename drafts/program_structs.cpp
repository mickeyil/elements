#include "program_structs.h"

bool initialize_program_runtime(
    Program& prog,
    const uint8_t* buffer_sizes,
    uint16_t buffer_count,
    const PixelViewSpec* pixel_view_specs,
    uint16_t pixel_view_count
)
{
    if (!prog.pixel_buffer_pool.initialize(buffer_sizes, buffer_count)) {
        return false;
    }

    if (!prog.pixel_views.initialize(
            prog.pixel_buffer_pool,
            pixel_view_specs,
            pixel_view_count)) {
        return false;
    }

    // TODO: final decoder should validate PixelView specs:
    // - spec.buffer_idx is valid
    // - spec.size <= pool.buffer_size(spec.buffer_idx) for identity views
    // - every spec.indices[j] is within pool.buffer_size(spec.buffer_idx)

    // TODO: final decoder should resolve canonical layer buffers from the pool
    // and validate that each Layer receives:
    // - a valid resolved buffer pointer
    // - a buffer_length derived from that resolved pool buffer size
    // - a physical_map whose length matches the canonical layer length

    // TODO: final decoder should validate event view references:
    // - dst_pixv_idx != PIXV_NONE
    // - dst_pixv_idx < prog.pixel_views.count()
    // - src_pixv_idx is either PIXV_NONE or < prog.pixel_views.count()
    // - work_pixv_idx is either PIXV_NONE or < prog.pixel_views.count()

    return true;
}

void free_program_sketch(Program* prog)
{
    if (prog == nullptr) {
        return;
    }

    if (prog->layers != nullptr) {
        delete[] prog->layers;
    }

    delete prog;
}
