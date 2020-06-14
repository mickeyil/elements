#ifndef __SENSORS_H__
#define __SENSORS_H__

#include "topicparser.h"
#include "sample.h"

typedef enum {
    SENSOR_NOT_AVAILABLE = 0,
    SENSOR_DISTANCE,
} sensor_type_t;

typedef struct {
    void *data;
    unsigned long timestamp;
} sensor_data_t;

#define echoPin D7 // Echo Pin
#define trigPin D6 // Trigger Pin

sensor_type_t sensor_type = SENSOR_NOT_AVAILABLE;


template <typename T>
bool in_range(T val, T min_val, T max_val)
{
    if (min_val <= val && val <= max_val)
        return true;
    return false;
}


void process_sensor_distance(PubSubClient& client, unsigned long time_ms, 
    const String& topic)
{
    static unsigned long last_ts = 0;
    static int once = 0;
    static Sample<int, 10> samples(0);
    static int counter = 0;
    static bool state_in_range = false;

    // FIXME: make it a variable read from mqtt
    static const int in_range_low = 40;
    static const int in_range_high = 60;

    #if 0
    if (once == 0) {
        setup_sensor_distance(sensor_data);
        once = 1;
        Serial.println("distance sensor is ready.");
    }
    #endif

    bool update_distance = false;
    if (time_ms - last_ts < 50) {
        return;
    }

    last_ts = time_ms;
    counter += 1;

    long duration, distance; // Duration used to calculate distance
    digitalWrite(trigPin, LOW);
    delayMicroseconds(2);
    digitalWrite(trigPin, HIGH);
    delayMicroseconds(10);
    digitalWrite(trigPin, LOW);
    duration = pulseIn(echoPin, HIGH);
    //Calculate the distance (in cm) based on the speed of sound.
    distance = duration/58.2;

    samples.sample(distance);
    int dmedian = samples.get_median();

    Serial.print("samples_in ");
    int votes_for_approach = samples.items_within_range(in_range_low, in_range_high, 10);
    Serial.print("samples_out");
    bool is_approach = in_range(dmedian, in_range_low, in_range_high);

    Serial.print("votes_for_approach: ");
    Serial.print(votes_for_approach);
    Serial.print("\t is_approace: ");
    Serial.println(is_approach);

    #if 1
    // Serial.print(counter);
    Serial.print("distance: ");
    Serial.print(distance);
    Serial.print("\tmedian-filtered: ");
    Serial.println(dmedian);
    #endif

    bool new_state_in_range = false;
    const char * response = "";
    if (is_approach && votes_for_approach > 6) {
        new_state_in_range = true;
    }

    String topic_distance = topic + String("/is_approaching");
    if (new_state_in_range == true && state_in_range == false) {
        // approaching: false -> true
        client.publish(topic_distance.c_str(), "true");
    } else if (new_state_in_range == false && state_in_range == true ) {
        // moving away: true -> false
        client.publish(topic_distance.c_str(), "false");
    }
    state_in_range = new_state_in_range;
}

void process_sensor(sensor_type_t sensor_type, PubSubClient& client, unsigned long time_ms,
    const String& topic)
{
    switch (sensor_type) {
        case SENSOR_DISTANCE:
            process_sensor_distance(client, time_ms, topic);
            break;
        default:
            Serial.print("unknown sensor type: ");
            Serial.println(sensor_type);
    }
}


void operation_handler_sensors(const TopicParser& tp, unsigned int topic_index, 
                           byte* payload, unsigned int length)
{
  const char * command = tp.get_topic_level(topic_index);
  
  Serial.print("topic index: ");
  Serial.println(topic_index);
  Serial.print("command: [");
  Serial.print(command);
  Serial.println("]");

  if (strncmp(command, "set_distance", MAXSTRLEN_COMMAND) == 0) {
    sensor_type = SENSOR_DISTANCE;  
    Serial.println("sensors: device is set as a distance sensor.");
  } else {
    Serial.print("unsupported command: [");
    Serial.print(command);
    Serial.println("]");
  }
}


#endif