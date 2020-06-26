#pragma once

#include "animation_manager.h"
#include "element_topics.h"

// contains pointers to various handlers
struct handlers_t {
  AnimationManager *panim_mgr;
  ElementTopics *ptopics;
  double t_now;

  handlers_t() : panim_mgr(0), ptopics(0), t_now(0.0) { }
};
