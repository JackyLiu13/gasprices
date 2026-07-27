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
//
// PINS: these default to GPIO2/3, which are free on the header of the
// Waveshare ESP32-C6-LCD-1.47. Do NOT set them to 6/7 on that board — those are
// the onboard LCD's SPI MOSI and SCLK, are not broken out, and scanning them
// reports ~120 phantom devices (see the "everything ACKs" note below).

#include <Arduino.h>
#include <Wire.h>

#define SDA_PIN 2
#define SCL_PIN 3

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
      }
      Serial.println();
      found++;
    }
  }

  if (found == 0) {
    Serial.println("  nothing found.");
    Serial.println("  check: VCC->3V3 (not 5V), GND->GND, SDA/SCL not swapped,");
    Serial.println("         and that SDA_PIN/SCL_PIN above match your wiring.");
  } else if (found > 8) {
    // A real bus has one or two devices. Dozens of ACKs, usually a different
    // set each pass, means SDA is reading low when the ACK is sampled — the bus
    // is not an I2C bus at all. Overwhelmingly this is wrong pins: something
    // else is already driving them, e.g. an onboard SPI display.
    Serial.printf("  %d device(s) -- THIS IS NOT %d DEVICES.\n", found, found);
    Serial.println("  'everything ACKs' means WRONG PINS, the same way");
    Serial.println("  'nothing found' means NOTHING CONNECTED.");
    Serial.println("  check: are SDA_PIN/SCL_PIN actually free on your board?");
    Serial.println("         on the ESP32-C6-LCD-1.47, GPIO6/7 are the LCD's SPI");
    Serial.println("         lines and are not broken out at all.");
  } else {
    Serial.printf("  %d device(s).\n", found);
  }

  Serial.println();
  delay(5000);
}
