#ifndef __ROUTEREQ_H__
#define __ROUTEREQ_H__

#include "topicparser.h"
#include "operate.h"

#define MAXSTRLEN_CATEGORY    16

void routerequest(char* topic, byte* payload, unsigned int length)
{
  TopicParser tp(topic);
  const char *category = tp.get_topic_level(2);

  if (strncmp(category, "operate", MAXSTRLEN_CATEGORY) == 0) {
    process_operation(tp, 3, payload, length);
  } else {
    Serial.print("unsupported category: [");
    Serial.print(category);
    Serial.println("]");
  }
}

#endif

