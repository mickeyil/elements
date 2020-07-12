#pragma once

#include <cstdint>
#include "handlers.h"
#include "topicparser.h"

void operation_handler_event(const TopicParser& tp, unsigned int topic_index, 
                              uint8_t* payload, unsigned int length, 
                              handlers_t& handlers, const char **errstr);