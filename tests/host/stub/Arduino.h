// Host stand-in for Arduino.h: lets the GENERATED firmware run on the PC in tests.
// Simulated: ADC per GPIO, millis(), Serial in/out, ESP chip info.
#pragma once
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>

#include <string>

#define ESP_ARDUINO_VERSION_MAJOR 3
#define ADC_11db 3

extern uint16_t g_adc[40];
extern uint32_t g_millis;
extern int g_adcAttenuationSet[40];

inline uint16_t analogRead(uint8_t pin) { return g_adc[pin]; }
inline uint32_t analogReadMilliVolts(uint8_t pin) { return (uint32_t)g_adc[pin] * 3100u / 4095u; }
inline void analogReadResolution(int) {}
inline void analogSetPinAttenuation(uint8_t pin, int a) { g_adcAttenuationSet[pin] = a; }
inline void delay(unsigned long ms) { g_millis += ms; }
inline void delayMicroseconds(unsigned int) {}
inline uint32_t millis() { return g_millis; }
inline uint32_t getCpuFrequencyMhz() { return 240; }

struct EspClass {
  const char* getChipModel() { return "ESP32-D0WD-V3"; }
  int getChipRevision() { return 3; }
};
extern EspClass ESP;

// Everything printed goes to stdout; input is queued by the harness.
struct SerialStub {
  std::string input;
  void begin(unsigned long) {}
  int available() { return (int)input.size(); }
  int read() {
    if (input.empty()) return -1;
    const char c = input[0];
    input.erase(0, 1);
    return (unsigned char)c;
  }
  int printf(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    const int n = vprintf(fmt, ap);
    va_end(ap);
    return n;
  }
  void println(const char* s = "") { ::printf("%s\n", s); }
  void print(const char* s) { ::printf("%s", s); }
};
extern SerialStub Serial;
