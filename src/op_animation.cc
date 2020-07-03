#include "requests.h"
#include "op_animation.h"


void cmd_animation_setup(uint8_t* payload, unsigned int length, handlers_t& handlers, 
                         const char **errstr);
void cmd_animation_add(uint8_t* payload, unsigned int length, handlers_t& handlers, 
                         const char **errstr);


void operation_handler_animation(const TopicParser& tp, unsigned int topic_index, 
                                 uint8_t* payload, unsigned int length, 
                                 handlers_t& handlers, const char **errstr)
{
  const char * command = tp.get_topic_level(topic_index);
  DPRINTF("animation handler: command: %s", command);
  
  if (strncmp(command, "setup", MAXSTRLEN_COMMAND) == 0) {
    cmd_animation_setup(payload, length, handlers, errstr);
  } else if (strncmp(command, "add", MAXSTRLEN_COMMAND) == 0) {
    cmd_animation_add(payload, length, handlers, errstr);
  } else {
    *errstr = "unsupported command";
    return;
  }
}


void cmd_animation_setup(uint8_t* payload, unsigned int length, handlers_t& handlers, 
                         const char **errstr)
{
  handlers.panim_mgr->setup_channel(payload, length, errstr);
}


void cmd_animation_add(uint8_t* payload, unsigned int length, handlers_t& handlers, 
                         const char **errstr)
{
  handlers.panim_mgr->add_animation(payload, length, handlers.t_now, errstr);
}

