#include <cassert>
#include <cstring>
#include "animation.h"


void Animation::render(float t_rel, float duration)
{
  assert(t_rel >= 0.0 && t_rel <= duration);
  
  // progress is the percent of relative time to the whole duration. 
  // values in [0.0, 1.0]
  float progress = 0.0;
  if (duration > 0.0) {
    progress = t_rel / duration;
  }

  const char *errstr = nullptr;
  _panim_mgr->execute(_code, progress, &errstr);
  assert(errstr == nullptr);   // CRUDE :/
}


void Animation::set(uint8_t* seqbuf, unsigned int len, const char **errstr)
{
  _code.resize(len);
  memcpy(&_code[0], seqbuf, len);
}
