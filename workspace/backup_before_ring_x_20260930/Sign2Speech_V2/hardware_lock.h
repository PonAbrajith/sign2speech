// hardware_lock.h - Sign2Speech V2 glove wiring lock.
//
// THIS FILE IS NOT GENERATED. It is copied verbatim into every firmware build
// and checks, at compile time, that config.h still describes the real glove.
// If any assertion fails, the generated configuration no longer matches the
// wiring: fix the configuration - do not edit these numbers unless the glove
// itself has been rewired (then change config/pins.py AND this file).
#pragma once

// ---- Flex sensors: Thumb, Index, Middle, Ring, Little ----------------------
static_assert(FLEX_COUNT == 5, "exactly five flex sensors");
static_assert(FLEX_PIN_THUMB == 32, "Thumb flex must be D32 / GPIO32");
static_assert(FLEX_PIN_INDEX == 35, "Index flex must be D35 / GPIO35");
static_assert(FLEX_PIN_MIDDLE == 34, "Middle flex must be D34 / GPIO34");
static_assert(FLEX_PIN_RING == 39, "Ring flex must be VN / GPIO39");
static_assert(FLEX_PIN_LITTLE == 36, "Little flex must be VP / GPIO36");
static_assert(FLEX_PINS[0] == 32 && FLEX_PINS[1] == 35 && FLEX_PINS[2] == 34 && FLEX_PINS[3] == 39 &&
              FLEX_PINS[4] == 36, "FLEX_PINS order must be {32, 35, 34, 39, 36}");

// ---- OLED: its own I2C bus --------------------------------------------------
static_assert(OLED_SDA_PIN == 18, "OLED SDA must be D18");
static_assert(OLED_SCL_PIN == 19, "OLED SCL must be D19");

// ---- MPU6050: a separate I2C bus -------------------------------------------
static_assert(MPU_SDA_PIN == 22, "MPU6050 SDA must be D22");
static_assert(MPU_SCL_PIN == 21, "MPU6050 SCL must be D21");
static_assert(MPU_ADDRESS == 0x68, "MPU6050 address is 0x68");

// ---- The two buses must never be merged ------------------------------------
static_assert(OLED_I2C_PORT != MPU_I2C_PORT, "OLED and MPU6050 must use separate TwoWire instances");
static_assert(OLED_SDA_PIN != MPU_SDA_PIN && OLED_SCL_PIN != MPU_SCL_PIN &&
              OLED_SDA_PIN != MPU_SCL_PIN && OLED_SCL_PIN != MPU_SDA_PIN,
              "OLED and MPU6050 must not share I2C pins");

// ---- No on-board audio in V2 (speech is on the laptop) ---------------------
#if defined(AUDIO_PIN) || defined(DAC_AUDIO_PIN) || defined(USE_DFPLAYER) || defined(USE_PAM8403)
#error "V2 has no ESP32 audio output: speech is produced by the Studio on the laptop speaker"
#endif
