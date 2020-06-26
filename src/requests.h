#pragma once
#include <cstdint>

#include "handlers.h"

#define MAXSTRLEN_CATEGORY    16
#define MAXSTRLEN_OPERATION   16
#define MAXSTRLEN_COMMAND     16

// process incoming request.
void process_request(char* topic, uint8_t* payload, unsigned int length, 
  handlers_t& handlers, const char **errstr);

