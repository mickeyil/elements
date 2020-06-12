#ifndef __SENSORS_H__
#define __SENSORS_H__

// #include "sensor_distance.h"

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

void setup_sensor_distance(sensor_data_t *sensor_data)
{
    pinMode(trigPin, OUTPUT);
    pinMode(echoPin, INPUT);

    sensor_data->data = malloc(sizeof(int));
    Serial.println("distance sensor initialized.");
}

void process_sensor_distance(sensor_data_t *sensor_data, unsigned long time_ms)
{
    static unsigned long last_ts = 0;
    static int once = 0;

    
    if (once == 0) {
        setup_sensor_distance(sensor_data);
        once = 1;
        Serial.println("distance sensor is ready.");
    }

    if (time_ms - last_ts < 50) {
        return;
    }
    last_ts = time_ms;

    long duration, distance; // Duration used to calculate distance
    digitalWrite(trigPin, LOW);
    delayMicroseconds(2);
    digitalWrite(trigPin, HIGH);
    delayMicroseconds(10);
    digitalWrite(trigPin, LOW);
    duration = pulseIn(echoPin, HIGH);
    //Calculate the distance (in cm) based on the speed of sound.
    distance = duration/58.2;

    int *pdata = static_cast<int*>(sensor_data->data);
    *pdata = distance;
    sensor_data->timestamp = time_ms;

    Serial.print("distance: ");
    Serial.println(distance);
}

void process_sensor(sensor_type_t sensor_type, sensor_data_t *sensor_data, unsigned long time_ms)
{
    switch (sensor_type) {
        case SENSOR_DISTANCE:
            process_sensor_distance(sensor_data, time_ms);
            break;
        default:
            Serial.print("unknown sensor type: ");
            Serial.println(sensor_type);
    }
}


#endif