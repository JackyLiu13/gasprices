// i2c_scan — find out what's actually on the I2C bus.
//
// Flash this BEFORE gasprices.ino if you don't know your display's address, or
// any time the main sketch prints "oled: not found". It answers the only two
// questions that matter: is the display wired correctly, and what address is it?
//
// Set the two pins below to whatever you wired, open Serial Monitor at 115200.
// Expect 0x3C (most common) or 0x3D. Nothing found = a wiring problem, not a
// software one — see INSTALL.md.
//
// Board: ESP32C6 Dev Module. Remember USB CDC On Boot -> Enabled, or this
// prints nothing at all.

#include <Arduino.h>
#include <Wire.h>

#define SDA_PIN 6
#define SCL_PIN 7

void setup() {
  Serial.begin(115200);
  delay(1500);           // give the USB CDC port time to enumerate
  Serial.println("\ni2c_scan");
  Serial.printf("SDA=GPIO%d  SCL=GPIO%d\n\n", SDA_PIN, SCL_PIN);
  Wire.begin(SDA_PIN, SCL_PIN);
}

void loop() {
  int found = 0;

  Serial.println("scanning 0x01..0x7E ...");
  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      Serial.printf("  device at 0x%02X", addr);
      if (addr == 0x3C || addr == 0x3D) {
        Serial.print("  <- looks like an SSD1306/SH1106 OLED");
        Serial.printf("  (set OLED_ADDR 0x%02X in config.h)", addr);
      }
      Serial.println();
      found++;
    }
  }

  if (found == 0) {
    Serial.println("  nothing found.");
    Serial.println("  check: VCC->3V3 (not 5V), GND->GND, SDA/SCL not swapped,");
    Serial.println("         and that SDA_PIN/SCL_PIN above match your wiring.");
  } else {
    Serial.printf("  %d device(s).\n", found);
  }

  Serial.println();
  delay(5000);
}
