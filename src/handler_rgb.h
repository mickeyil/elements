#ifndef __HANDLER_RGB_H__
#define __HANDLER_RGB_H__

#define FASTLED_ESP8266_DMA
#include "FastLED/FastLED.h"

#include "topicparser.h"

typedef struct {
  uint32_t req_id;
  uint32_t pin_num;
  uint32_t data_size;
} rgb_set_header_t;

#define MAXSTRLEN_COMMAND     16

#define RGB_ARRAY_SIZE        300

// buffer to hold the payload header.
// it is copied due to 4-byte alignment requirement.
uint8_t *header_buf[sizeof(rgb_set_header_t)];

// rgb data
CRGB rgb_array[RGB_ARRAY_SIZE];

void cmd_set_rgb(byte* payload, unsigned int length);


// handle rgb operations
void operation_handler_rgb(const TopicParser& tp, unsigned int topic_index, 
                           byte* payload, unsigned int length)
{
  const char * command = tp.get_topic_level(topic_index);
  
  if (strncmp(command, "set", MAXSTRLEN_COMMAND) == 0) {
    cmd_set_rgb(payload, length);
  } else {
    Serial.print("unsupported command: [");
    Serial.print(command);
    Serial.println("]");
  }
}


void cmd_set_rgb(byte* payload, unsigned int length)
{
  size_t header_size = sizeof(rgb_set_header_t);
  if (length < header_size) {
    Serial.print("payload smaller than header size: ");
    Serial.println(length);
    return;
  }
  
  // due to a fixed buffer size limit
  if (length > 1024) {
    return;
  }
  
  // payload is not 4-byte aligned - reading the header directly from it may 
  // cause LoadStore Exception.
  memcpy(header_buf, payload, header_size);

  rgb_set_header_t *header = reinterpret_cast<rgb_set_header_t*>(header_buf);
  
  unsigned int data_size = length - header_size;
  if (data_size != header->data_size) {
    Serial.println("data size mismatch.");
    return;
  }

  if (data_size % 3 != 0) {
    Serial.print("invalid data size, expecting 3*N: ");
    Serial.println(data_size);
    return;
  }

  unsigned int num_pixels = data_size / 3;

  // rgb_byte_array sees rgb_array of CRGB struct as an array of bytes
  byte * rgb_byte_array = reinterpret_cast<byte*>(&rgb_array);
  
  // copy pixel data from payload to the rgb_array
  memcpy(rgb_byte_array, payload + header_size, length - header_size);

  for (unsigned int lidx = 0; lidx < num_pixels; lidx++) {
    int r = rgb_byte_array[3*lidx];
    int g = rgb_byte_array[3*lidx + 1];
    int b = rgb_byte_array[3*lidx + 2];
    
    Serial.print("LED offset ");
    Serial.print(lidx);
    Serial.print(" R: ");
    Serial.print(r);
    Serial.print(" G: ");
    Serial.print(g);
    Serial.print(" B: ");
    Serial.println(b);
  }
  FastLED.show();
}

#endif

