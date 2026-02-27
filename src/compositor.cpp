#include "compositor.h"

Compositor::Compositor(Strip& strip)
    : _strip(strip), _num_layers(0)
{
    for (uint8_t i = 0; i < MAX_LAYERS; i++) {
        _layers[i] = nullptr;
    }
}

void Compositor::add_layer(Layer* layer)
{
    if (_num_layers >= MAX_LAYERS) return;
    _layers[_num_layers++] = layer;
    sort_layers();
}

void Compositor::remove_layer(uint8_t id)
{
    for (uint8_t i = 0; i < _num_layers; i++) {
        if (_layers[i]->id() == id) {
            // shift remaining layers down
            for (uint8_t j = i; j < _num_layers - 1; j++) {
                _layers[j] = _layers[j + 1];
            }
            _layers[_num_layers - 1] = nullptr;
            _num_layers--;
            return;
        }
    }
}

void Compositor::render(float t)
{
    _strip.clear();

    for (uint8_t li = 0; li < _num_layers; li++) {
        Layer* layer = _layers[li];
        Animation* anim = layer->animation();
        if (anim == nullptr) continue;

        // Let the animation write into the layer's HSVA buffer
        anim->render(layer->buffer(), layer->length(), t);

        // Blend layer into strip (alpha compositing in RGB space)
        const uint8_t* idx_map = layer->index_map();
        hsva_t* buf = layer->buffer();

        for (uint8_t i = 0; i < layer->length(); i++) {
            float a = buf[i].a;
            if (a <= 0.0f) continue;  // fully transparent, skip

            uint8_t phys = idx_map[i];
            rgb_t fg = hsv_to_rgb(buf[i].h, buf[i].s, buf[i].v);

            if (a >= 1.0f) {
                _strip.set_rgb(phys, fg);
            } else {
                rgb_t bg = _strip.get_rgb(phys);
                _strip.set_rgb(phys, rgb_lerp(bg, fg, a));
            }
        }
    }

    // Apply gamma correction once, after all blending is done
    for (uint16_t i = 0; i < _strip.length(); i++) {
        _strip.set_rgb(i, gamma_correct(_strip.get_rgb(i)));
    }
}

// Simple insertion sort by priority (lowest first). Called on add.
void Compositor::sort_layers()
{
    for (uint8_t i = 1; i < _num_layers; i++) {
        Layer* key = _layers[i];
        int j = i - 1;
        while (j >= 0 && _layers[j]->priority() > key->priority()) {
            _layers[j + 1] = _layers[j];
            j--;
        }
        _layers[j + 1] = key;
    }
}
