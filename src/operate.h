#ifndef __OPERATE_H__
#define __OPERATE_H__

#include "topicparser.h"

// operation handlers
#include "handler_rgb.h"

#define MAXSTRLEN_OPERATION   16

void process_operation(const TopicParser& tp, unsigned int topic_index, 
                       byte* payload, unsigned int length)
{
  const char * operation = tp.get_topic_level(topic_index);

  if (strncmp(operation, "rgb", MAXSTRLEN_OPERATION) == 0) {
    operation_handler_rgb(tp, 4, payload, length);
  } else {
    Serial.print("unsupported operation: [");
    Serial.print(operation);
    Serial.println("]");
  }

}
#endif

