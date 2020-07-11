#pragma once
#include <cstdio>
#include <Arduino.h>
#include <NTPClient.h>

// #include "sample.h"

class SyncedTime {
  public:
    SyncedTime(NTPClient* pntpclient) : _pntpclient(pntpclient),  _time_offset_lf(0.0) { }
      //_offset_samples(0) { }

    void sync() {
      _pntpclient->update();
      double time_device_lf = static_cast<double>(millis()) / 1000.0;
      double time_ntp_lf = static_cast<double>(_pntpclient->getEpochTime());
      time_ntp_lf += static_cast<double>(_pntpclient->getEpochMSec())/1000.0;
      _time_offset_lf = time_ntp_lf - time_device_lf; 
      // _offset_samples.sample(_time_offset_lf);
    }

    double get_time_lf() const {
      double time_device_lf = (double) millis() / 1000.0;
      return _time_offset_lf + time_device_lf;
      #if 0
      double t_now = static_cast<double>(millis());
      Serial.print ("get_time_lf(): millis(): ");
      Serial.print(t_now);
      Serial.print(" time offset: ");
      Serial.print(_time_offset);
      t_now += _time_offset;
      return t_now / 1000.0;
      #endif
    }

    #if 0
    void print_samples() const {
      Serial.print("SyncedTime samples: ");
      for (unsigned int i = 0; i < _offset_samples.size(); i++) {
        Serial.print(_offset_samples.get_item(i));
      }
      Serial.println("");
    }
    #endif

  private:
    NTPClient *_pntpclient;
    double _time_offset_lf;
    //  Sample<double, 8> _offset_samples;
};

