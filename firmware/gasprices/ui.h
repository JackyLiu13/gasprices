// ui.h — everything that touches the LCD. Kept out of the .ino so the screen
// layout can be fiddled with without wading through the network code.
//
// Panel: onboard 1.47" ST7789, 172x320 native, driven in landscape as 320x172.
// The controller is a 240x320 part, so a 172-wide panel sits centred with a
// 34px column offset. Adafruit_ST7789::init(172, 320) works that out itself
// (the "centred" branch covers 1.47" panels) — always init in native portrait
// and rotate afterwards, or the offset lands on the wrong axis.
//
// Layout, 320x172:
//   y   0..10   header: station, and how old the data is
//   y  14       divider
//   y  22..54   today's price, size 4        | right: level %, and a level bar
//   y  62..99   verdict bar, filled in the verdict colour
//   y 106..121  one line of plain English
//   y 126..134  tank state                   | right: the wait/jump number
//   y 138..168  sparkline of the rolling window
#ifndef GP_UI_H
#define GP_UI_H

#include <Adafruit_GFX.h>
#include <Adafruit_ST7789.h>

#include "verdict.h"

// Native panel geometry. Landscape is what we actually draw in.
#define LCD_NATIVE_W 172
#define LCD_NATIVE_H 320
#define LCD_W 320
#define LCD_H 172

// RGB565. The LED colours in verdict.h are tuned for a 48/255-dimmed WS2812 and
// look muddy on a backlit panel, so the screen gets its own brighter set.
#define C_BLACK   0x0000
#define C_WHITE   0xFFFF
#define C_GREY    0x8410
#define C_DIM     0x39E7
#define C_RED     0xF800
#define C_GREEN   0x07E0
#define C_AMBER   0xFD20
#define C_PURPLE  0x780F
#define C_CYAN    0x07FF

static Adafruit_ST7789 lcd(&SPI, LCD_CS_PIN, LCD_DC_PIN, LCD_RST_PIN);

// Verdict -> panel colour. Same green/amber/red semantics as the LED so the two
// never disagree at a glance.
static uint16_t uiVerdictColor(Verdict v) {
  switch (v) {
    case V_FILL_NOW:
    case V_GREAT:     return C_GREEN;
    case V_NEUTRAL:   return C_AMBER;
    case V_WAIT:
    case V_EXPENSIVE: return C_RED;
    default:          return C_PURPLE;
  }
}

// Text that stays legible on top of uiVerdictColor().
static uint16_t uiOnVerdict(Verdict v) {
  uint16_t c = uiVerdictColor(v);
  return (c == C_GREEN || c == C_AMBER) ? C_BLACK : C_WHITE;
}

static int16_t uiTextW(const char *s, uint8_t size) {
  return (int16_t)strlen(s) * 6 * size;
}

// Draw `s` so its right edge lands on x=right.
static void uiRightText(const char *s, int16_t right, int16_t y, uint8_t size,
                        uint16_t color) {
  lcd.setTextSize(size);
  lcd.setTextColor(color);
  lcd.setCursor(right - uiTextW(s, size), y);
  lcd.print(s);
}

static bool uiBegin(void) {
  pinMode(LCD_BL_PIN, OUTPUT);
  digitalWrite(LCD_BL_PIN, HIGH);          // backlight; low leaves a black panel

  SPI.begin(LCD_SCLK_PIN, -1, LCD_MOSI_PIN, LCD_CS_PIN);
  lcd.init(LCD_NATIVE_W, LCD_NATIVE_H);    // portrait first, so the 34px offset lands right
  lcd.setSPISpeed(40000000);
  lcd.setRotation(LCD_ROTATION);
  lcd.setTextWrap(false);
  lcd.fillScreen(C_BLACK);
  // The panel is soldered to the board and has no readback line, so there is
  // nothing to probe: if the sketch is running, the display is present.
  return true;
}

// Full-screen single message, for boot and hard failures.
static void uiMessage(const char *title, const char *detail) {
  lcd.fillScreen(C_BLACK);
  lcd.setTextSize(3);
  lcd.setTextColor(C_WHITE);
  lcd.setCursor((LCD_W - uiTextW(title, 3)) / 2, 58);
  lcd.print(title);
  if (detail) {
    lcd.setTextSize(2);
    lcd.setTextColor(C_GREY);
    lcd.setCursor((LCD_W - uiTextW(detail, 2)) / 2, 96);
    lcd.print(detail);
  }
}

// hist[] is the rolling window in tenths of a cent; the last entry is today.
static void uiSparkline(const int32_t *hist, uint8_t n, int16_t x, int16_t y,
                        int16_t w, int16_t h, uint16_t line, uint16_t dot) {
  if (n < 2) return;

  int32_t lo = hist[0], hi = hist[0];
  for (uint8_t i = 1; i < n; i++) {
    if (hist[i] < lo) lo = hist[i];
    if (hist[i] > hi) hi = hist[i];
  }
  int32_t span = hi - lo;
  if (span <= 0) span = 1;

  // Inset by two pixels on every side so the radius-2 "today" dot at the last
  // point still lands entirely on the panel instead of being clipped in half.
  int16_t x0 = x + 2, x1 = x + w - 3;
  int16_t y0 = y + 2, y1 = y + h - 3;
  if (x1 <= x0 || y1 <= y0) return;

  int16_t prevX = 0, prevY = 0;
  for (uint8_t i = 0; i < n; i++) {
    int16_t px = x0 + (int16_t)((int32_t)i * (x1 - x0) / (n - 1));
    // Inverted: a high price plots high on screen.
    int16_t py = y1 - (int16_t)((hist[i] - lo) * (y1 - y0) / span);
    if (i > 0) lcd.drawLine(prevX, prevY, px, py, line);
    prevX = px;
    prevY = py;
  }
  // Fat dot on today so the eye lands on "where am I in this range".
  lcd.fillCircle(prevX, prevY, 2, dot);
}

// bestLabel/bestSave describe which station the price on screen belongs to.
// today/window/hist are all already priced at that station, so the header names
// the number rather than adding a competing one.
// bestLabel/bestSave describe the station currently on screen. today/window/hist
// are already priced at it, so the header names the number rather than adding a
// competing one. bestSave may be negative when browsing a pricier station.
static void uiRender(const GpInput *in, const GpVerdict *v,
                     const int32_t *hist, uint8_t histLen, bool online,
                     const char *bestLabel = nullptr, int32_t bestSave = 0,
                     bool bestConfident = true,
                     uint8_t stationIdx = 0, uint8_t stationCount = 0) {
  char buf[40], tmp[16];
  const uint16_t vc = uiVerdictColor(v->verdict);

  lcd.fillScreen(C_BLACK);

  // --- header: which station this price is for ---
  lcd.setTextSize(1);
  lcd.setTextColor(C_CYAN);
  lcd.setCursor(6, 2);
  if (bestLabel && bestLabel[0]) {
    lcd.print(bestLabel);
    if (!bestConfident) lcd.print(F(" ?"));   // offset from very few samples
    if (stationCount > 1) {
      snprintf(buf, sizeof buf, " %u/%u",
               (unsigned)(stationIdx + 1), (unsigned)stationCount);
      lcd.setTextColor(C_DIM);
      lcd.print(buf);
    }
  } else {
    lcd.print(F("RICHMOND HILL"));
  }
  if (!online) {
    uiRightText("OFF", LCD_W - 6, 2, 1, C_AMBER);
  } else if (in->age_minutes >= 0) {
    snprintf(buf, sizeof buf, "%ldh", (long)(in->age_minutes / 60));
    uiRightText(buf, LCD_W - 6, 2, 1, C_GREY);
  }
  lcd.drawFastHLine(0, 14, LCD_W, C_DIM);

  // --- today's price, size 4: 6 glyphs * 24px = 144px ---
  gp_fmt_price(in->today, buf, sizeof buf);
  lcd.setTextSize(4);
  lcd.setTextColor(C_WHITE);
  lcd.setCursor(6, 22);
  lcd.print(buf);

  // --- right column: level % over a proportional bar ---
  if (v->level_pct >= 0) {
    snprintf(buf, sizeof buf, "LVL %ld%%", (long)v->level_pct);
    uiRightText(buf, LCD_W - 6, 24, 2, C_GREY);

    const int16_t bx = 170, by = 46, bw = LCD_W - 6 - bx, bh = 12;
    lcd.drawRect(bx, by, bw, bh, C_DIM);
    int32_t fill = ((int32_t)(bw - 2) * v->level_pct) / 100;
    if (fill > 0) lcd.fillRect(bx + 1, by + 1, (int16_t)fill, bh - 2, vc);
  }

  // --- verdict bar, filled so it reads at a glance across the room ---
  lcd.fillRect(0, 62, LCD_W, 38, vc);
  const char *name = gp_verdict_name(v->verdict);
  lcd.setTextSize(3);
  lcd.setTextColor(uiOnVerdict(v->verdict));
  lcd.setCursor((LCD_W - uiTextW(name, 3)) / 2, 69);
  lcd.print(name);

  // --- reason: 21 chars max, size 2 = 252px of the 320 available ---
  gp_reason(in, v, buf, sizeof buf);
  lcd.setTextSize(2);
  lcd.setTextColor(C_WHITE);
  lcd.setCursor(6, 106);
  lcd.print(buf);

  // --- tank state, and the wait/jump number ---
  lcd.setTextSize(1);
  lcd.setTextColor(C_GREY);
  lcd.setCursor(6, 126);
  lcd.print(in->tank == TANK_FULL ? F("TANK FULL")
            : in->tank == TANK_LOW ? F("TANK LOW") : F("TANK HALF"));

  // Right of the tank row: the saving from driving here instead of the usual
  // station. This is the bigger number in practice — the spread across town
  // runs several times the day-to-day timing swing — so it wins the slot over
  // the wait countdown, which the reason line already spells out.
  if (bestSave > 0) {
    gp_fmt_cents(bestSave, tmp, sizeof tmp);
    snprintf(buf, sizeof buf, "SAVE %s", tmp);
    uiRightText(buf, LCD_W - 6, 126, 1, C_GREEN);
  } else if (bestSave < 0) {
    // Browsing a station dearer than your usual one — say so in red rather
    // than showing nothing, so cycling never looks like it stopped working.
    gp_fmt_cents(-bestSave, tmp, sizeof tmp);
    snprintf(buf, sizeof buf, "+%s", tmp);
    uiRightText(buf, LCD_W - 6, 126, 1, C_RED);
  } else if (stationCount > 0) {
    uiRightText("USUAL", LCD_W - 6, 126, 1, C_GREY);
  } else if (v->days_to_wait > 0) {
    gp_fmt_cents(v->save, tmp, sizeof tmp);
    snprintf(buf, sizeof buf, "%dd %s", (int)v->days_to_wait, tmp);
    uiRightText(buf, LCD_W - 6, 126, 1, C_GREY);
  } else if (v->tomorrow_jump != 0) {
    gp_fmt_cents(v->tomorrow_jump < 0 ? -v->tomorrow_jump : v->tomorrow_jump,
                 tmp, sizeof tmp);
    snprintf(buf, sizeof buf, "%c%s", v->tomorrow_jump > 0 ? '^' : 'v', tmp);
    uiRightText(buf, LCD_W - 6, 126, 1, C_GREY);
  }

  // --- sparkline ---
  uiSparkline(hist, histLen, 6, 138, LCD_W - 12, 30, C_GREY, vc);
}

#endif  // GP_UI_H
