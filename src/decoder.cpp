#include "decoder.h"
#include <cstring>
#include <cstdlib>
#include <new>

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

namespace {

struct BlobReader {
    const uint8_t* data;
    size_t len;
    size_t pos;

    BlobReader(const uint8_t* d, size_t l) : data(d), len(l), pos(0) {}

    bool has(size_t n) const { return pos + n <= len; }

    uint8_t read_u8() {
        return data[pos++];
    }

    uint16_t read_u16() {
        uint16_t v;
        memcpy(&v, data + pos, 2);
        pos += 2;
        return v;
    }

    float read_f32() {
        float v;
        memcpy(&v, data + pos, 4);
        pos += 4;
        return v;
    }

    void read_bytes(uint8_t* dst, size_t n) {
        memcpy(dst, data + pos, n);
        pos += n;
    }
};

bool parse_wave_params(BlobReader& r, WaveParams& p) {
    if (!r.has(33)) return false;
    p.channel    = r.read_u8();
    p.h          = r.read_f32();
    p.s          = r.read_f32();
    p.v          = r.read_f32();
    p.min_val    = r.read_f32();
    p.max_val    = r.read_f32();
    p.period     = r.read_f32();
    p.phase0     = r.read_f32();
    p.pixel_step = r.read_f32();
    return true;
}

bool parse_spark_params(BlobReader& r, SparkParams& p) {
    if (!r.has(16)) return false;
    p.color_h = r.read_f32();
    p.color_s = r.read_f32();
    p.color_v = r.read_f32();
    p.fade    = r.read_f32();
    return true;
}

bool parse_shift_params(BlobReader& r, ShiftParams& p) {
    if (!r.has(23)) return false;
    p.direction    = r.read_u8();
    p.velocity     = r.read_f32();
    p.circular     = r.read_u8();
    p.fill_h       = r.read_f32();
    p.fill_s       = r.read_f32();
    p.fill_v       = r.read_f32();
    p.fill_a       = r.read_f32();
    p.buffer_id    = r.read_u8();
    return true;
}

bool parse_paint_params(BlobReader& r, PaintParams& p) {
    if (!r.has(1)) return false;
    p.mode = r.read_u8();
    if (p.mode == 0) {
        if (!r.has(16)) return false;
        p.color_h = r.read_f32();
        p.color_s = r.read_f32();
        p.color_v = r.read_f32();
        p.color_a = r.read_f32();
        p.pixel_count = 0;
        p.pixels = nullptr;
    } else {
        if (!r.has(1)) return false;
        p.pixel_count = r.read_u8();
        if (!r.has(p.pixel_count * 16)) return false;
        p.pixels = new (std::nothrow) hsva_t[p.pixel_count];
        if (!p.pixels) return false;
        for (uint8_t i = 0; i < p.pixel_count; i++) {
            p.pixels[i].h = r.read_f32();
            p.pixels[i].s = r.read_f32();
            p.pixels[i].v = r.read_f32();
            p.pixels[i].a = r.read_f32();
        }
        p.color_h = p.color_s = p.color_v = p.color_a = 0;
    }
    return true;
}

} // anonymous namespace

// ---------------------------------------------------------------------------
// decode_program
// ---------------------------------------------------------------------------

Program* decode_program(const uint8_t* blob, size_t len) {
    BlobReader r(blob, len);

    // Header: 12 bytes
    if (!r.has(12)) return nullptr;

    // Magic
    if (blob[0] != 'E' || blob[1] != 'L' || blob[2] != 'E' || blob[3] != 'M')
        return nullptr;
    r.pos = 4;

    uint8_t version = r.read_u8();
    if (version != BLOB_VERSION) return nullptr;

    uint8_t layer_count      = r.read_u8();
    uint8_t buffer_count     = r.read_u8();
    uint8_t max_remap_length = r.read_u8();
    float   duration         = r.read_f32();

    if (layer_count > 32) return nullptr;

    // Allocate Program
    Program* prog = new (std::nothrow) Program();
    if (!prog) return nullptr;

    prog->duration         = duration;
    prog->layer_count      = layer_count;
    prog->max_remap_length = max_remap_length;
    prog->layers           = nullptr;
    prog->temp_buffer      = nullptr;
    prog->pool.count       = buffer_count;
    prog->pool.sizes       = nullptr;
    prog->pool.buffers     = nullptr;

    // Buffer pool
    if (buffer_count > 0) {
        if (!r.has(buffer_count)) goto fail;

        prog->pool.sizes   = new (std::nothrow) uint8_t[buffer_count];
        prog->pool.buffers = new (std::nothrow) hsva_t*[buffer_count];
        if (!prog->pool.sizes || !prog->pool.buffers) goto fail;

        for (uint8_t i = 0; i < buffer_count; i++) {
            prog->pool.buffers[i] = nullptr;
        }

        for (uint8_t i = 0; i < buffer_count; i++) {
            uint8_t sz = r.read_u8();
            prog->pool.sizes[i] = sz;
            prog->pool.buffers[i] = new (std::nothrow) hsva_t[sz];
            if (!prog->pool.buffers[i]) goto fail;
            // Zero-init buffer
            memset(prog->pool.buffers[i], 0, sz * sizeof(hsva_t));
        }
    }

    // Temp render buffer
    if (max_remap_length > 0) {
        prog->temp_buffer = new (std::nothrow) hsva_t[max_remap_length];
        if (!prog->temp_buffer) goto fail;
    }

    // Layers
    prog->layers = new (std::nothrow) LayerDef[layer_count]();
    if (!prog->layers) goto fail;

    for (uint8_t li = 0; li < layer_count; li++) {
        LayerDef& layer = prog->layers[li];
        layer.index_map = nullptr;
        layer.events    = nullptr;
        layer.buffer    = nullptr;

        // Index map
        if (!r.has(1)) goto fail;
        layer.index_map_length = r.read_u8();

        if (!r.has(layer.index_map_length)) goto fail;
        layer.index_map = new (std::nothrow) uint8_t[layer.index_map_length];
        if (!layer.index_map) goto fail;
        r.read_bytes(layer.index_map, layer.index_map_length);

        // Event count
        if (!r.has(2)) goto fail;
        layer.event_count = r.read_u16();

        // Events
        layer.events = new (std::nothrow) AnimationEvent[layer.event_count]();
        if (!layer.events) goto fail;

        for (uint16_t ei = 0; ei < layer.event_count; ei++) {
            AnimationEvent& event = layer.events[ei];
            event.remap = nullptr;

            // anim_type (1) + t_start (4) + duration (4) + source_layer (1) +
            // remap_is_identity (1) = 11
            if (!r.has(11)) goto fail;

            uint8_t anim_type = r.read_u8();
            event.params.type = static_cast<AnimType>(anim_type);
            event.t_start     = r.read_f32();
            event.duration    = r.read_f32();
            event.source_layer = r.read_u8();
            if (event.source_layer != SOURCE_NONE && event.source_layer >= layer_count)
                goto fail;
            event.remap_is_identity = (r.read_u8() != 0);

            // Remap
            if (!r.has(1)) goto fail;
            event.remap_length = r.read_u8();

            if (!r.has(event.remap_length)) goto fail;
            event.remap = new (std::nothrow) uint8_t[event.remap_length];
            if (!event.remap) goto fail;
            r.read_bytes(event.remap, event.remap_length);

            // Params
            if (!r.has(1)) goto fail;
            uint8_t params_size = r.read_u8();

            if (!r.has(params_size)) goto fail;
            size_t params_start = r.pos;

            bool ok = false;
            switch (event.params.type) {
                case ANIM_WAVE:  ok = parse_wave_params(r, event.params.wave);   break;
                case ANIM_SHIFT: ok = parse_shift_params(r, event.params.shift); break;
                case ANIM_SPARK: ok = parse_spark_params(r, event.params.spark); break;
                case ANIM_PAINT: ok = parse_paint_params(r, event.params.paint); break;
                default: goto fail;
            }
            if (!ok) goto fail;

            if (event.params.type == ANIM_SHIFT &&
                event.params.shift.buffer_id >= buffer_count)
                goto fail;

            // Advance to end of params (in case of padding)
            r.pos = params_start + params_size;
        }
    }

    return prog;

fail:
    free_program(prog);
    return nullptr;
}

// ---------------------------------------------------------------------------
// free_program
// ---------------------------------------------------------------------------

void free_program(Program* prog) {
    if (!prog) return;

    if (prog->layers) {
        for (uint8_t li = 0; li < prog->layer_count; li++) {
            LayerDef& layer = prog->layers[li];
            if (layer.events) {
                for (uint16_t ei = 0; ei < layer.event_count; ei++) {
                    delete[] layer.events[ei].remap;
                    if (layer.events[ei].params.type == ANIM_PAINT)
                        delete[] layer.events[ei].params.paint.pixels;
                }
                delete[] layer.events;
            }
            delete[] layer.index_map;
            delete[] layer.buffer;
        }
        delete[] prog->layers;
    }

    if (prog->pool.buffers) {
        for (uint8_t i = 0; i < prog->pool.count; i++) {
            delete[] prog->pool.buffers[i];
        }
        delete[] prog->pool.buffers;
    }
    delete[] prog->pool.sizes;

    delete[] prog->temp_buffer;

    delete prog;
}
