#include <cstdint>

#include "op_event.h"
#include "requests.h"
#include "event_manager.h"
#include "topicparser.h"
#include "utils.h"

void operation_handler_event(const TopicParser& tp, unsigned int topic_index, 
                                 uint8_t* payload, unsigned int length, 
                                 handlers_t& handlers, const char **errstr)
{
  const char * command = tp.get_topic_level(topic_index);
  DPRINTF("event handler: command: %s", command);
  
  if (strncmp(command, "add", MAXSTRLEN_COMMAND) == 0) {
    handlers.pevent_mgr->add_event(payload, length, errstr);
  } else {
    *errstr = "unsupported command";
    return;
  }
}
