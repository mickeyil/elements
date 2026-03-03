#pragma once

#include "strip.h"
#include "layer.h"

#define MAX_LAYERS 32

// The compositor blends all active layers into the strip every frame.
// Layers are composited by priority (lowest first). All blending is alpha-based.
// The strip is cleared to black before compositing; the background layer writes
// with A=1.0, overlays use intermediate alpha values.

class Compositor {
public:
    Compositor(Strip& strip);

    void add_layer(Layer* layer);
    void remove_layer(uint8_t id);

    // Render all layers and composite into the strip.
    // t: seconds since program start.
    void render(float t);

private:
    Strip& _strip;
    Layer* _layers[MAX_LAYERS];
    uint8_t _num_layers;

    void sort_layers();
};
