// ui.h — everything that touches the SSD1306. Kept out of the .ino so the
// screen layout can be fiddled with without wading through the network code.
//
// Layout, 128x64:
//   y 0..8    header: station, and how old the data is
//   y 9..25   today's price, double size, with level% / wait info to the right
//   y 27..38  inverted verdict bar
//   y 41..49  one line of plain English
//   y 52..63  sparkline of the rolling window
#ifndef GP_UI_H
#define GP_UI_H

#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

#include "verdict.h"

#define OLED_W 128
#define OLED_H 64

static Adafruit_SSD1306 oled(OLED_W, OLED_H, &Wire, -1);

static bool uiBegin(uint8_t i2cAddr) {
  if (!oled.begin(SSD1306_SWITCHCAPVCC, i2cAddr)) return false;
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  oled.setTextWrap(false);
  oled.display();
  return true;
}

// Full-screen single message, for boot and hard failures.
static void uiMessage(const char *title, const char *detail) {
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  oled.setTextSize(1);
  oled.setCursor(0, 24);
  oled.println(title);
  if (detail) {
    oled.setCursor(0, 40);
    oled.println(detail);
  }
  oled.display();
}

// hist[] is the rolling window in tenths of a cent; the last entry is today.
static void uiSparkline(const int32_t *hist, uint8_t n, int16_t x, int16_t y,
                        int16_t w, int16_t h) {
  if (n < 2) return;

  int32_t lo = hist[0], hi = hist[0];
  for (uint8_t i = 1; i < n; i++) {
    if (hist[i] < lo) lo = hist[i];
    if (hist[i] > hi) hi = hist[i];
  }
  int32_t span = hi - lo;
  if (span <= 0) span = 1;

  // Inset by one pixel on every side so the radius-1 "today" dot at the last
  // point still lands entirely on the panel instead of being clipped in half.
  int16_t x0 = x + 1, x1 = x + w - 2;
  int16_t y0 = y + 1, y1 = y + h - 2;
  if (x1 <= x0 || y1 <= y0) return;

  int16_t prevX = 0, prevY = 0;
  for (uint8_t i = 0; i < n; i++) {
    int16_t px = x0 + (int16_t)((int32_t)i * (x1 - x0) / (n - 1));
    // Inverted: a high price plots high on screen.
    int16_t py = y1 - (int16_t)((hist[i] - lo) * (y1 - y0) / span);
    if (i > 0) oled.drawLine(prevX, prevY, px, py, SSD1306_WHITE);
    prevX = px;
    prevY = py;
  }
  // Fat dot on today so the eye lands on "where am I in this range".
  oled.fillCircle(prevX, prevY, 1, SSD1306_WHITE);
}

static void uiRender(const GpInput *in, const GpVerdict *v,
                     const int32_t *hist, uint8_t histLen, bool online) {
  char buf[40], tmp[16];

  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);

  // --- header ---
  oled.setTextSize(1);
  oled.setCursor(0, 0);
  oled.print(F("RICHMOND HILL"));
  if (!online) {
    oled.setCursor(OLED_W - 6 * 3, 0);
    oled.print(F("OFF"));
  } else if (in->age_minutes >= 0) {
    snprintf(buf, sizeof buf, "%ldh", (long)(in->age_minutes / 60));
    oled.setCursor(OLED_W - 6 * (int16_t)strlen(buf), 0);
    oled.print(buf);
  }

  // --- today's price ---
  gp_fmt_price(in->today, buf, sizeof buf);
  oled.setTextSize(2);
  oled.setCursor(0, 9);
  oled.print(buf);

  // --- right column: level, and the wait/jump number ---
  oled.setTextSize(1);
  if (v->level_pct >= 0) {
    snprintf(buf, sizeof buf, "LVL%3ld%%", (long)v->level_pct);
    oled.setCursor(86, 10);
    oled.print(buf);
  }
  // Only 7 glyphs fit between x=86 and the right edge, so: "2d 3.0c" when
  // there's a dip to wait for, else tomorrow's move as ^up / vdown.
  if (v->days_to_wait > 0) {
    gp_fmt_cents(v->save, tmp, sizeof tmp);
    snprintf(buf, sizeof buf, "%dd %s", (int)v->days_to_wait, tmp);
  } else if (v->tomorrow_jump != 0) {
    gp_fmt_cents(v->tomorrow_jump < 0 ? -v->tomorrow_jump : v->tomorrow_jump,
                 tmp, sizeof tmp);
    snprintf(buf, sizeof buf, "%c%s", v->tomorrow_jump > 0 ? '^' : 'v', tmp);
  } else {
    buf[0] = '\0';
  }
  if (buf[0]) {
    oled.setCursor(86, 19);
    oled.print(buf);
  }

  // --- verdict bar, inverted so it reads at a glance across the room ---
  oled.fillRect(0, 27, OLED_W, 12, SSD1306_WHITE);
  oled.setTextColor(SSD1306_BLACK);
  oled.setTextSize(1);
  const char *name = gp_verdict_name(v->verdict);
  int16_t nameW = (int16_t)strlen(name) * 6;
  oled.setCursor((OLED_W - nameW) / 2, 30);
  oled.print(name);
  oled.setTextColor(SSD1306_WHITE);

  // --- reason ---
  gp_reason(in, v, buf, sizeof buf);
  oled.setCursor(0, 41);
  oled.print(buf);

  // --- sparkline ---
  uiSparkline(hist, histLen, 0, 52, OLED_W, 12);

  oled.display();
}

#endif  // GP_UI_H
