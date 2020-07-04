#include <cstdint>

#include "op_sensor.h"
#include "requests.h"
#include "sensor_manager.h"
#include "topicparser.h"
#include "utils.h"

void operation_handler_sensor(const TopicParser& tp, unsigned int topic_index, 
                                 uint8_t* payload, unsigned int length, 
                                 handlers_t& handlers, const char **errstr)
{
  const char * command = tp.get_topic_level(topic_index);
  DPRINTF("sensor handler: command: %s", command);
  
  if (strncmp(command, "add", MAXSTRLEN_COMMAND) == 0) {
    handlers.psensor_mgr->add_sensor(payload, length, errstr);
  } else {
    *errstr = "unsupported command";
    return;
  }
}
