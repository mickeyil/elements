#include <cassert>
#include <cstring>

#include "sensor.h"
#include "distance.h"
#include "utils.h"

#ifdef ARDUINO
#include <Arduino.h>


static void initialize_sensor_pins(uint8_t trig_pin, uint8_t echo_pin)
{
  pinMode(trig_pin, OUTPUT);
  pinMode(echo_pin, INPUT);
}


static int sensor_read_cm(uint8_t trig_pin, uint8_t echo_pin)
{
    long duration, distance; // Duration used to calculate distance
    digitalWrite(trig_pin, LOW);
    delayMicroseconds(2);
    digitalWrite(trig_pin, HIGH);
    delayMicroseconds(10);
    digitalWrite(trig_pin, LOW);
    duration = pulseIn(echo_pin, HIGH);
    //Calculate the distance (in cm) based on the speed of sound.
    distance = duration/58.2;
    return static_cast<int>(distance);
}
#else
static const int sim_distance_values[] = {
  13, 13, 13, 13, 13, 55, 66, 77, 88,
  // 55, 66, 77, 88, 23, 23, 23, 23, 23, 23, 23, 23, 23, 
  // 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 
  // 12, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 
};

static void initialize_sensor_pins(uint8_t trig_pin, uint8_t echo_pin)
{
  DPRINTF("initialize_sensor_pins(): trig: %u, echo: %u", trig_pin, echo_pin);
}

// for simulation, rotate between values
static int sensor_read_cm(uint8_t trig_pin, uint8_t echo_pin)
{
  static int counter = 0;
  static const unsigned int nvals = sizeof(sim_distance_values) / sizeof(sim_distance_values[0]);
  int value = sim_distance_values[counter++ % nvals];
  return value;
}
#endif


void SensorDistance::_setup(void *params, unsigned int size)
{
  assert(sizeof(dist_params_t) == size);
  memcpy(&_params, params, sizeof(dist_params_t));

  DPRINTF("_setup(): distance sensor will use: %s pin for trigger, %s pin for echo.",
    dev_pin_map_str[_params.trigger_dpin], dev_pin_map_str[_params.echo_dpin]);

  _trig_pin = dev_pin_map[_params.trigger_dpin];
  _echo_pin = dev_pin_map[_params.echo_dpin];
  initialize_sensor_pins(_trig_pin, _echo_pin);
}


void SensorDistance::_process(handlers_t& handlers)
{
  if (_trig_pin == SENSOR_UNINITIALIZED_PIN ||
      _echo_pin == SENSOR_UNINITIALIZED_PIN) {
    return;
  }
  _distance_cm = sensor_read_cm(_trig_pin, _echo_pin);

  _val_as_int = static_cast<int16_t>(_distance_cm);
}
