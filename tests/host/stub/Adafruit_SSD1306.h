// Host stand-in for Adafruit_SSD1306: records which bus/address it was started on
// and prints the displayed text as "@OLED line1|line2" on every display().
#pragma once
#include <stdio.h>
#include <string>

#include "Wire.h"

#define SSD1306_SWITCHCAPVCC 0x02
#define SSD1306_WHITE 1

class Adafruit_SSD1306 {
 public:
  Adafruit_SSD1306(int w, int h, TwoWire* twi, int rst) : wire_(twi) { (void)w, (void)h, (void)rst; }
  bool begin(uint8_t, uint8_t addr, bool = true, bool periphBegin = true) {
    if (periphBegin) wire_->begin();  // would re-init the bus with default pins: must not happen
    ::printf("@OLED_BEGIN port=%d sda=%d scl=%d addr=0x%02X\n", wire_->port(), wire_->sda(), wire_->scl(), addr);
    return wire_->bus.present[addr];
  }
  void clearDisplay() { text_.clear(); }
  void setTextColor(int) {}
  void setTextSize(int) {}
  void setCursor(int, int) {}
  void println(const char* s) {
    if (!text_.empty()) text_ += "|";
    text_ += s;
  }
  void display() { ::printf("@OLED %s\n", text_.c_str()); }

 private:
  TwoWire* wire_;
  std::string text_;
};
