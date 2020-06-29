#!/bin/bash
set -e
set -x

CFLAGS="-Wno-unused-function -DDEBUG_HELPERS"
SRC_FILES="src/animation_manager.cc src/channel.cc src/requests.cc src/slotsmm.cc src/pixelarray.cc src/strip.cc src/colors.cc src/animation.cc src/animations/anim_fill.cc"

g++ -Wall -g $CFLAGS -I . -I src/ -o test/sim_anim test/sim_anim.cpp $SRC_FILES -lpaho-mqttpp3 -lpaho-mqtt3as

