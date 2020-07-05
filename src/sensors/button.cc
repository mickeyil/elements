#include <cassert>
#include <cstring>

#include "sensor.h"
#include "button.h"
#include "utils.h"

#ifdef ARDUINO
#include <Arduino.h>


static void initialize_sensor_pins(uint8_t button_pin)
{
  pinMode(button_pin, INPUT);
}


static int sensor_read(uint8_t button_pin)
{
    return digitalRead(button_pin);
}
#else
static const int sim_button_values[] = {
  0, 1
};

static void initialize_sensor_pins(uint8_t button_pin)
{
  DPRINTF("initialize_sensor_pins(): button: %u", button_pin);
}

// for simulation, rotate between values
static int sensor_read(uint8_t button_pin)
{
  static int counter = 0;
  static const unsigned int nvals = sizeof(sim_button_values) / sizeof(sim_button_values[0]);
  int value = sim_button_values[counter++ % nvals];
  return value;
}
#endif


void SensorButton::_setup(void *params, unsigned int size)
{
  assert(sizeof(button_params_t) == size);
  memcpy(&_params, params, sizeof(button_params_t));

  DPRINTF("_setup(): distance sensor will use: %s pin for button input",
    dev_pin_map_str[_params.button_dpin]);

  _button_pin = dev_pin_map[_params.button_dpin];
  initialize_sensor_pins(_button_pin);
}


void SensorButton::_process(handlers_t& handlers)
{
  if (_button_pin == SENSOR_UNINITIALIZED_PIN) 
  {
    return;
  }
  _state = sensor_read(_button_pin);
}
