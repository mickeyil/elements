#include "platform_clock.h"

#include <esp_timer.h>

int64_t platform_clock::now_us()
{
    return esp_timer_get_time();
}
