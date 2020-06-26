#pragma once

#include <cstdio>
#include <cstring>

#define MAX_ELEMENT_TOPIC 100
#define CHIPID_LEN 13

#define ELEMENTS_PREFIX "elements"


class ElementTopics
{
  public:
    ElementTopics() : _prefix_len(0) { }

    void set_chipid(const char *chipid) {
      snprintf(_topic_buf, MAX_ELEMENT_TOPIC, "%s/%s/", ELEMENTS_PREFIX, chipid);
      _prefix_len = strlen(_topic_buf);
    }

    const char * get_full_topic(const char *partial_topic) {
      strncpy(&_topic_buf[_prefix_len], partial_topic, MAX_ELEMENT_TOPIC-_prefix_len-1);
      int pt_len = strlen(partial_topic);
      _topic_buf[_prefix_len + pt_len] = 0;
      return _topic_buf;
    }

  private:
    char _topic_buf[MAX_ELEMENT_TOPIC];
    unsigned int _prefix_len;
};
