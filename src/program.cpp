#include "program.h"

Program::~Program()
{
    delete[] layers;
}

void free_program(Program* prog)
{
    delete prog;
}
