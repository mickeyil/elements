#pragma once

#include <cstdint>
#include <cstring>
#include "colors.h"
#include "animation.h"

// A layer combines pixel addressing, color data, and compositing priority.
// It owns an HSVA buffer sized to its pixel count (not full strip length).
// An animation renders into the buffer; the compositor blends it into the strip.

class Layer {
public:
    // index_map: array of physical strip indices (copied internally)
    // length: number of logical pixels in this layer
    // priority: compositing order (lower = further back)
    Layer(uint8_t id, const uint8_t* index_map, uint8_t length, uint8_t priority);
    ~Layer();

    uint8_t id() const { return _id; }
    uint8_t priority() const { return _priority; }
    uint8_t length() const { return _length; }
    const uint8_t* index_map() const { return _index_map; }
    hsva_t* buffer() { return _buffer; }

    void set_animation(Animation* anim) { _animation = anim; }
    Animation* animation() const { return _animation; }

    void clear_buffer() {
        for (uint8_t i = 0; i < _length; i++) {
            _buffer[i] = hsva_t(0, 0, 0, 0);
        }
    }

private:
    uint8_t _id;
    uint8_t _priority;
    uint8_t _length;
    uint8_t* _index_map;
    hsva_t* _buffer;
    Animation* _animation;  // not owned
};
