#pragma once

#include <cstdint>

#include "handler_result.h"

class WireReader;
class BackgroundStore;

// Pass-through for category 0x2_ commands (StoreBackground,
// ClearBackground) into BackgroundStore.

class StorageHandler {
public:
    explicit StorageHandler(BackgroundStore& store);

    HandlerResult handle(uint8_t opcode, WireReader& r);

private:
    HandlerResult handle_store_background_(WireReader& r);
    HandlerResult handle_clear_background_(WireReader& r);

    BackgroundStore& _store;
};
