#!/bin/bash

# setup paho mqtt c and c++ clients
set -e
set -x

sudo apt-get install build-essential gcc make cmake cmake-curses-gui
sudo apt-get install libssl-dev
sudo apt-get install doxygen graphviz
sudo apt-get install libcppunit-dev


TMPDIR="/tmp/cpaho"
mkdir -p $TMPDIR && cd $TMPDIR
git clone https://github.com/eclipse/paho.mqtt.c.git
cd paho.mqtt.c
git checkout v1.3.1
cmake -Bbuild -H. -DPAHO_WITH_SSL=ON -DPAHO_ENABLE_TESTING=OFF
sudo cmake --build build/ --target install
sudo ldconfig

git clone https://github.com/eclipse/paho.mqtt.cpp
cd paho.mqtt.cpp
cmake -Bbuild -H. -DPAHO_BUILD_DOCUMENTATION=TRUE -DPAHO_BUILD_SAMPLES=TRUE
sudo cmake --build build/ --target install
sudo ldconfig

