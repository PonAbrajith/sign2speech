// Host stand-in for Wire.h: two independent simulated I2C buses.
// Each TwoWire only sees the devices placed on ITS bus, so the tests can prove
// the OLED is driven on port 1 (SDA 18 / SCL 19) and the MPU6050 on port 0 (SDA 22 / SCL 21).
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

struct SimBus {
  bool present[128] = {};
  uint8_t mpuRegs[128] = {};     // register file of an MPU6050 at 0x68 on this bus (if present)
};

class TwoWire {
 public:
  explicit TwoWire(int port) : port_(port) {}
  bool begin(int sda, int scl, uint32_t freq = 0) {
    sda_ = sda;
    scl_ = scl;
    ::printf("@WIRE port=%d sda=%d scl=%d freq=%lu\n", port_, sda, scl, (unsigned long)freq);
    return true;
  }
  bool begin() {
    ::printf("@WIRE port=%d begin() WITHOUT PINS\n", port_);
    return true;
  }
  void setClock(uint32_t) {}
  void beginTransmission(uint16_t addr) {
    addr_ = (uint8_t)addr;
    nWritten_ = 0;
  }
  size_t write(uint8_t b) {
    if (nWritten_ < sizeof(written_)) written_[nWritten_++] = b;
    return 1;
  }
  size_t write(const uint8_t* data, size_t n) {
    for (size_t i = 0; i < n; i++) write(data[i]);
    return n;
  }
  uint8_t endTransmission(bool = true) {
    if (sda_ < 0) return 4;                      // bus never started
    if (!bus.present[addr_]) return 2;           // NACK on address
    if (addr_ == 0x68 && nWritten_ >= 1) {
      reg_ = written_[0];
      if (nWritten_ >= 2) bus.mpuRegs[reg_] = written_[1];
    }
    return 0;
  }
  size_t requestFrom(uint16_t addr, size_t len, bool = true) {
    if (sda_ < 0 || !bus.present[addr]) return 0;
    readPos_ = reg_;
    readLeft_ = len;
    return len;
  }
  int read() {
    if (!readLeft_) return -1;
    readLeft_--;
    return bus.mpuRegs[readPos_++ & 0x7F];
  }
  int available() { return (int)readLeft_; }
  int port() const { return port_; }
  int sda() const { return sda_; }
  int scl() const { return scl_; }

  SimBus bus;

 private:
  int port_;
  int sda_ = -1, scl_ = -1;
  uint8_t addr_ = 0, reg_ = 0, readPos_ = 0;
  uint8_t written_[32];
  size_t nWritten_ = 0, readLeft_ = 0;
};

extern TwoWire Wire;
extern TwoWire Wire1;
