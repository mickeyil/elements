#pragma once

#define MAX_TOPIC_LEN 128
#define MAX_TOPIC_DEPTH 16

class TopicParser
{
 public:
  TopicParser(const char *topic) : depth(0)
  {
    strncpy(topic_tokens, topic, MAX_TOPIC_LEN);
    
    // delimeter char in mqtt topic
    const char *delim = "/";
    char *token;

    token = strtok(topic_tokens, delim);
    unsigned int token_idx = 0;

    while (token != NULL)
    {
      topic_levels[token_idx] = token;
      token_idx++;
      token = strtok(NULL, delim);
    }
    depth = token_idx;
    for (int i = depth; i < MAX_TOPIC_DEPTH; i++) {
      topic_levels[i] = NULL;
    }
  }
  
  // returns the depth of topic. i.e. : elemets/MOCK-1/status is of depth 3
  // for convenience, levels start from 0: ^0    ^1     ^2
  unsigned int get_depth() const { return depth; }
  
  const char * get_topic_level(unsigned int level) const
  {
    if (level >= depth) {
      return NULL;
    } else {
      return topic_levels[level];
    }
  }

 private:
  unsigned int depth;
  const char *topic_levels[MAX_TOPIC_DEPTH];
  char topic_tokens[MAX_TOPIC_LEN];
};


