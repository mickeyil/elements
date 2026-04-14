#include "program_structs.h"

Program::~Program()
{
    delete[] layers;
    layers = nullptr;
    layer_count = 0;
}

// Program assembly lives in drafts/decoder.cpp. The decoder reads each
// section directly into the right container (PixelBufferPool, PixelViews,
// CopyOps, Layer events) while validating per-element rules. There is no
// separate "build a Program from already-parsed inputs" helper.

void free_program(Program* prog)
{
    delete prog;
}
