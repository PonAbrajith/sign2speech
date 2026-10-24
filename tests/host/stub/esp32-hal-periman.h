#pragma once
#include <stdint.h>
enum { ESP32_BUS_TYPE_INIT = 0, ESP32_BUS_TYPE_ADC_ONESHOT = 5 };
inline int perimanGetPinBusType(uint8_t) { return ESP32_BUS_TYPE_ADC_ONESHOT; }
