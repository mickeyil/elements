#include "core/copy_ops.h"

#include <new>

CopyOps::~CopyOps()
{
    reset();
}

bool CopyOps::initialize(const CopyOp* ops, uint16_t count)
{
    reset();

    if (count == 0) {
        return true;
    }
    if (ops == nullptr) {
        return false;
    }

    _ops = new (std::nothrow) CopyOp[count];
    if (_ops == nullptr) {
        return false;
    }
    _count = count;

    for (uint16_t i = 0; i < count; i++) {
        _ops[i] = ops[i];
    }

    return true;
}

void CopyOps::reset()
{
    delete[] _ops;
    _ops = nullptr;
    _count = 0;
}
