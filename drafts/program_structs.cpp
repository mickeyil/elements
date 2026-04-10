#include "program_structs.h"

bool initialize_program_views(
    Program& prog,
    const uint16_t* buffer_sizes,
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

    for (uint8_t li = 0; li < prog.layer_count; li++) {
        LayerDef& layer = prog.layers[li];
        layer.buffer = prog.pixel_buffer_pool.buffer_at(layer.buffer_idx);

        // TODO: final decoder should validate that:
        // - layer.buffer_idx is valid
        // - layer.buffer_length matches the intended canonical layer buffer size
        // - physical_map length == buffer_length
    }

    // TODO: final decoder should validate PixelView specs:
    // - spec.buffer_idx is valid
    // - spec.size <= pool.buffer_size(spec.buffer_idx) for identity views
    // - every spec.indices[j] is within pool.buffer_size(spec.buffer_idx)

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
        for (uint8_t li = 0; li < prog->layer_count; li++) {
            LayerDef& layer = prog->layers[li];

            if (layer.events != nullptr) {
                for (uint16_t ei = 0; ei < layer.event_count; ei++) {
                    delete layer.events[ei].animation;
                }
                delete[] layer.events;
            }

            delete[] layer.physical_map;
        }
        delete[] prog->layers;
    }

    delete prog;
}
