#pragma once

#include "topicparser.h"
#include "sample.h"


typedef enum {
    SENSOR_NOT_AVAILABLE = 0,
    SENSOR_DISTANCE,
    SENSOR_BUTTON,
} sensor_type_t;


void process_sensor(sensor_type_t sensor_type, PubSubClient& client, unsigned long time_ms,
    const String& topic);
