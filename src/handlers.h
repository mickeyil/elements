#pragma once

#include "animation_manager.h"
#include "element_topics.h"


typedef void (*publish_func_t)(const char *topic, const char *payload_cstr);


// contains pointers to various handlers
struct handlers_t {
  AnimationManager *panim_mgr;
  ElementTopics *ptopics;
  double t_now;
  publish_func_t publish_func;

  handlers_t() : panim_mgr(0), ptopics(0), t_now(0.0), publish_func(nullptr) { }
};

