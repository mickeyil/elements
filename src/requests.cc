#include <cstring>
#include "requests.h"
#include "topicparser.h"
#include "utils.h"

#include "op_animation.h"
#include "op_sensor.h"
#include "op_event.h"

void process_operation(const TopicParser& tp, unsigned int topic_index, 
                       uint8_t* payload, unsigned int length, handlers_t& handlers, 
                       const char **errstr);

                         

void  process_request(char* topic, uint8_t* payload, unsigned int length, 
  handlers_t& handlers, const char **errstr)
{
  DPRINTF("request time: %lf", handlers.t_now);

  TopicParser tp(topic);
  const char *category = tp.get_topic_level(2);

  DPRINTF("process_request: categoty: %s", category);

  if (strncmp(category, "operate", MAXSTRLEN_CATEGORY) == 0) {
    process_operation(tp, 3, payload, length, handlers, errstr);
  } else {
    *errstr = "unsupported category";
    return;
  }
}


void process_operation(const TopicParser& tp, unsigned int topic_index, 
                       uint8_t* payload, unsigned int length, handlers_t& handlers, 
                       const char **errstr)
{
  const char * operation = tp.get_topic_level(topic_index);
  bool operation_supported = false;

  if (strncmp(operation, "animation", MAXSTRLEN_OPERATION) == 0) {
    operation_supported = true;
    operation_handler_animation(tp, 4, payload, length, handlers, errstr);
  } 
  
  else if (strncmp(operation, "sensors", MAXSTRLEN_OPERATION) == 0 ) {
    operation_supported = true;
    operation_handler_sensor(tp, 4, payload, length, handlers, errstr);
  }
  
  else if (strncmp(operation, "events", MAXSTRLEN_OPERATION) == 0 ) {
    operation_supported = true;
    operation_handler_event(tp, 4, payload, length, handlers, errstr);
  }
  
  if (!operation_supported) {
    *errstr = "unsupported operation";
    return;
  }
}

