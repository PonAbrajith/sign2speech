// Runs the GENERATED Sign2Speech V2 firmware on the PC.
//
// Build: g++ -std=gnu++14 -I stub -I <sketch dir> harness.cpp -o harness
// Args:  [oled=3C|3D|none] [mpu=yes|no]
// Stdin script, one command per line:
//   SETUP                 call setup()
//   ADC t i m r l         set the ADC value on the THUMB/INDEX/MIDDLE/RING/LITTLE pins (32,35,34,39,36)
//   RUN n                 run n sample periods (20 ms each) through loop()
//   CMD text              send "text\n" to the firmware's serial input
//   MARK text             print "@MARK text" (lets the test split the output)
#include <Arduino.h>
#include <Wire.h>

uint16_t g_adc[40];
uint32_t g_millis = 0;
int g_adcAttenuationSet[40];
EspClass ESP;
SerialStub Serial;
TwoWire Wire(0);
TwoWire Wire1(1);

#include "Sign2Speech_V2.ino"

#include <iostream>
#include <sstream>

int main(int argc, char** argv) {
  std::string oled = "3C";
  bool mpu = true;
  for (int i = 1; i < argc; i++) {
    std::string a = argv[i];
    if (a.rfind("oled=", 0) == 0) oled = a.substr(5);
    if (a == "mpu=no") mpu = false;
  }
  // Devices: MPU6050 only on bus 0 (Wire), OLED only on bus 1 (Wire1).
  if (mpu) {
    Wire.bus.present[0x68] = true;
    Wire.bus.mpuRegs[0x75] = 0x70;                    // this glove's WHO_AM_I
    const uint8_t data[14] = {0x00, 0x10, 0x00, 0x20, 0x40, 0x00, 0, 0, 0x00, 0x83, 0xFF, 0x7D, 0x00, 0x00};
    for (int i = 0; i < 14; i++) Wire.bus.mpuRegs[0x3B + i] = data[i];
  }
  if (oled == "3C") Wire1.bus.present[0x3C] = true;
  if (oled == "3D") Wire1.bus.present[0x3D] = true;
  setvbuf(stdout, nullptr, _IOFBF, 1 << 16);

  std::string line;
  while (std::getline(std::cin, line)) {
    if (!line.empty() && line.back() == '\r') line.pop_back();
    std::istringstream in(line);
    std::string cmd;
    in >> cmd;
    if (cmd == "SETUP") {
      setup();
    } else if (cmd == "ADC") {
      int v[5];
      for (int i = 0; i < 5; i++) in >> v[i];
      const int pins[5] = {32, 35, 34, 39, 36};
      for (int i = 0; i < 5; i++) g_adc[pins[i]] = (uint16_t)v[i];
    } else if (cmd == "RUN") {
      int n = 0;
      in >> n;
      for (int i = 0; i < n; i++) {  // exactly one sample per step
        g_millis += SAMPLE_PERIOD_MS;
        loop();
      }
    } else if (cmd == "CMD") {
      Serial.input += line.substr(4) + "\n";
      loop();
    } else if (cmd == "MARK") {
      printf("@MARK %s\n", line.substr(5).c_str());
    }
  }
  fflush(stdout);
  return 0;
}
