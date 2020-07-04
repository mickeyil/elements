#pragma once

//#include "animation_manager.h"
//#include "sensor_manager.h"
//#include "element_topics.h"

class SensorManager;
class AnimationManager;
class ElementTopics;
class Publisher;

typedef void (*publish_func_t)(const char *topic, const char *payload_cstr);


// contains pointers to various handlers
struct handlers_t {
  AnimationManager *panim_mgr;
  ElementTopics *ptopics;
  double t_now;
  Publisher *publisher;
  SensorManager *psensor_mgr;

  handlers_t() : panim_mgr(0), ptopics(0), t_now(0.0), publisher(0),
    psensor_mgr(0) { }
};

