#include "program_structs.h"

#include <new>

bool initialize_program_views(
    Program& prog,
    const uint16_t* buffer_sizes,
    uint16_t buffer_count
)
{
    if (!prog.pixel_buffer_pool.initialize(buffer_sizes, buffer_count)) {
        return false;
    }

    if (prog.pixel_view_count == 0) {
        return true;
    }
    if (prog.pixel_view_defs == nullptr) {
        return false;
    }

    prog.pixel_views = new (std::nothrow) PixelView[prog.pixel_view_count];
    if (prog.pixel_views == nullptr) {
        return false;
    }

    for (uint16_t i = 0; i < prog.pixel_view_count; i++) {
        const PixelViewDef& def = prog.pixel_view_defs[i];
        hsva_t* backing = prog.pixel_buffer_pool.buffer_at(def.buffer_idx);
        if (backing == nullptr) {
            return false;
        }

        // TODO: final decoder should also validate:
        // - def.buffer_idx is valid
        // - def.size <= pool.buffer_size(def.buffer_idx) for identity views
        // - every def.indices[j] is within pool.buffer_size(def.buffer_idx)
        prog.pixel_views[i].bind(
            backing,
            def.is_identity ? nullptr : def.indices,
            def.size
        );
    }

    for (uint8_t li = 0; li < prog.layer_count; li++) {
        LayerDef& layer = prog.layers[li];
        layer.buffer = prog.pixel_buffer_pool.buffer_at(layer.buffer_idx);

        // TODO: final decoder should validate that:
        // - layer.buffer_idx is valid
        // - layer.buffer_length matches the intended canonical layer buffer size
        // - physical_map length == buffer_length
    }

    // TODO: final decoder should validate event view references:
    // - dst_pixv_idx != PIXV_NONE
    // - dst_pixv_idx < prog.pixel_view_count
    // - src_pixv_idx is either PIXV_NONE or < prog.pixel_view_count
    // - work_pixv_idx is either PIXV_NONE or < prog.pixel_view_count

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

    if (prog->pixel_view_defs != nullptr) {
        for (uint16_t i = 0; i < prog->pixel_view_count; i++) {
            delete[] prog->pixel_view_defs[i].indices;
        }
        delete[] prog->pixel_view_defs;
    }

    delete[] prog->pixel_views;

    // PixelBufferPool owns its storage and cleans it up itself.
    prog->pixel_buffer_pool.reset();

    delete prog;
}
