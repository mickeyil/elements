// built with: g++ -g -Wall -o test_topicparser test_topicparser.cc
#include <cstdio>
#include <cstring>

#include "topicparser.h"

void print_topic(const char *topic)
{
  TopicParser tp(topic);
  printf("topic: %s\n", topic);
  printf("topic depth: %u\n", tp.get_depth());
  for (unsigned int i = 0; i < tp.get_depth(); i++) {
    printf("[%d]: [%s]\n", i, tp.get_topic_level(i));
  }
}

int main()
{
  const char *topics[4] = {
    "elements/ESP-12345678/operate/rgb/set",
    "elements/ESP-12345678/status",
    "elements/ESP-12345678/operate/rgb/dim/sub/category",
    "elements",
  };

  for (int i = 0; i < 4; i++) {
    print_topic(topics[i]);
  }
  return 0;
}

