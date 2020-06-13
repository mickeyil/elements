#ifndef __OPERATE_H__
#define __OPERATE_H__

#include "topicparser.h"

// operation handlers
#include "handler_rgb.h"
#include "sensors.h"

#define MAXSTRLEN_OPERATION   16

void process_operation(const TopicParser& tp, unsigned int topic_index, 
                       byte* payload, unsigned int length)
{
  const char * operation = tp.get_topic_level(topic_index);
  bool operation_supported = false;

  if (strncmp(operation, "rgb", MAXSTRLEN_OPERATION) == 0) {
    operation_supported = true;
    Serial.println("operation rgb");
    operation_handler_rgb(tp, 4, payload, length);
  } 
  
  if (strncmp(operation, "sensors", MAXSTRLEN_OPERATION) == 0 ) {
    operation_supported = true;
    Serial.println("operation sensors");
    operation_handler_sensors(tp, 4, payload, length);
  }
  
  if (!operation_supported) {
    Serial.print("unsupported operation: [");
    Serial.print(operation);
    Serial.println("]");
  }

}
#endif

