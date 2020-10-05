# elements
In this playground project LEDs and sensors that are connected to a wifi based board - ESP8266 are called 'elements'. 
The elements communicate via wifi & MQTT and are controlled by a device on their local network (raspberry pi).

Currently one scenario is implemented: distance sensor triggering an animation on a LED strip (ws2812) when someone gets near.

test/sim_anim is a C++ simulation intended for easier debugging on a PC rathen than the ESP device.
