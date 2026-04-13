#include "program_structs.h"

bool initialize_program_runtime(
    Program& prog,
    const uint16_t* buffer_sizes,
    uint16_t buffer_count,
    const PixelViewSpec* pixel_view_specs,
    uint16_t pixel_view_count,
    const CopyOp* copy_ops,
    uint16_t copy_op_count
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

    if (!prog.copy_ops.initialize(copy_ops, copy_op_count)) {
        return false;
    }

    // TODO: final decoder should validate PixelView specs:
    // - spec.buffer_idx is valid
    // - spec.size <= pool.buffer_size(spec.buffer_idx) for identity storage
    // - every storage_indices[j] is within pool.buffer_size(spec.buffer_idx)
    // - every physical_indices[j] is within HardwareProfile::strip_length
    // - physical mapping is present for every event dst view

    // TODO: final decoder should validate event view references:
    // - dst_pixv_idx != PIXV_NONE
    // - dst_pixv_idx < prog.pixel_views.count()
    // - dst PixelView has physical mapping
    // - src_pixv_idx is either PIXV_NONE or < prog.pixel_views.count()
    // - work_pixv_idx is either PIXV_NONE or < prog.pixel_views.count()

    // TODO: final decoder should validate copy ops:
    // - src/dst view indices are valid and not PIXV_NONE
    // - src.size() == dst.size()
    // - ops are sorted by op.at
    // - dst is storage-only unless a real future use case needs compositable
    //   copy destinations

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
