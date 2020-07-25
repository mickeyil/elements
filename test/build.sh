#!/bin/bash
set -e
set -x

CFLAGS="-Wall -Wno-unused-function -Wno-unused-variable -DDEBUG_HELPERS"
SRC_FILES="src/animation_manager.cc src/requests.cc src/slotsmm.cc src/pixelarray.cc src/strip.cc src/colors.cc src/animation.cc src/animation_sequence.cc "
SRC_FILES+="src/op_animation.cc src/sensor.cc src/sensors/distance.cc src/sensor_manager.cc src/op_sensor.cc src/sensors/button.cc src/event_manager.cc src/event.cc "
SRC_FILES+="src/events/range.cc src/op_event.cc"

g++ -g $CFLAGS -I . -I src/ -o bin/sim_anim test/sim_anim.cpp $SRC_FILES -lpaho-mqttpp3 -lpaho-mqtt3as

