#pragma once

#include <cstdint>

#include "handler_result.h"

namespace controller_link {

class WireReader;
class SessionHandler;
class BackgroundStore;

// Pass-through handler for category 0x2_. Routes StoreBackground /
// ClearBackground to the platform's BackgroundStore impl. Requires
// session attachment.

class StorageHandler {
public:
    StorageHandler(BackgroundStore& store, const SessionHandler& session);

    HandlerResult handle(uint8_t opcode, WireReader& r);

private:
    HandlerResult handle_store_background_(WireReader& r);
    HandlerResult handle_clear_background_(WireReader& r);

    BackgroundStore&      _store;
    const SessionHandler& _session;
};

}  // namespace controller_link
