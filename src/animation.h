#pragma once

#include "colors.h"

// Animation interface. Primitives implement this.
// The animation receives an HSVA buffer of length N and writes into it.
// It sees an isolated pixel world — no knowledge of physical layout.

class Animation {
public:
    virtual ~Animation() {}

    // Render into the given HSVA buffer at time t (seconds since animation start).
    virtual void render(hsva_t* buffer, uint8_t length, float t) = 0;
};
