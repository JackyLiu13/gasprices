// ui.h — everything that touches the LCD. Kept out of the .ino so the screen
// layout can be fiddled with without wading through the network code.
//
// Panel: onboard 1.47" ST7789, 172x320 native, driven in landscape as 320x172.
// The controller is a 240x320 part, so a 172-wide panel sits centred with a
// 34px column offset. Adafruit_ST7789::init(172, 320) works that out itself
// (the "centred" branch covers 1.47" panels) — always init in native portrait
// and rotate afterwards, or the offset lands on the wrong axis.
//
// Where everything sits is layout.json's business, not this file's: every
// coordinate below comes from an L_* const in the generated layout.h, so the
// layout can be dragged around in the browser preview (see preview/README.md)
// without touching C. What stays here is everything a coordinate cannot express
// — when an element is drawn at all, how the right-aligned ones are measured,
// how the bar and the sparkline are scaled, and what colour anything is.
//
// Layout, 320x172, as currently generated:
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

#include "layout.h"
#include "verdict.h"

// Native panel geometry. Landscape is what we actually draw in.
#define LCD_NATIVE_W 172
#define LCD_NATIVE_H 320
#define LCD_W 320
#define LCD_H 172

// The panel size is soldered-down hardware, so layout.json does not get to
// disagree about it. Catch that at compile time rather than as a screen full of
// clipped pixels.
#if LCD_W != L_PANEL_W || LCD_H != L_PANEL_H
#error "layout.json panel size disagrees with LCD_W/LCD_H"
#endif

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
  lcd.setTextSize(L_message_title.size);
  lcd.setTextColor(C_WHITE);
  lcd.setCursor((LCD_W - uiTextW(title, L_message_title.size)) / 2,
                L_message_title.y);
  lcd.print(title);
  if (detail) {
    lcd.setTextSize(L_message_detail.size);
    lcd.setTextColor(C_GREY);
    lcd.setCursor((LCD_W - uiTextW(detail, L_message_detail.size)) / 2,
                  L_message_detail.y);
    lcd.print(detail);
  }
}

// hist[] is the rolling window in tenths of a cent; the last entry is today.
static void uiSparkline(const int32_t *hist, uint8_t n, int16_t x, int16_t y,
                        int16_t w, int16_t h, int16_t dotR, uint16_t line,
                        uint16_t dot) {
  if (n < 2) return;

  int32_t lo = hist[0], hi = hist[0];
  for (uint8_t i = 1; i < n; i++) {
    if (hist[i] < lo) lo = hist[i];
    if (hist[i] > hi) hi = hist[i];
  }
  int32_t span = hi - lo;
  if (span <= 0) span = 1;

  // Inset by the dot radius on every side so the "today" dot at the last point
  // still lands entirely on the panel instead of being clipped in half. This is
  // the bug the host harness was written to catch, so keep it tied to dotR.
  int16_t x0 = x + dotR, x1 = x + w - dotR - 1;
  int16_t y0 = y + dotR, y1 = y + h - dotR - 1;
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
  lcd.fillCircle(prevX, prevY, dotR, dot);
}

// bestLabel/bestSave describe the station currently on screen. today/window/hist
// are already priced at it, so the header names the number rather than adding a
// competing one. bestSave may be negative when browsing a pricier station.
static void uiRender(const GpInput *in, const GpVerdict *v,
                     const int32_t *hist, uint8_t histLen, bool online,
                     const char *bestLabel = nullptr, int32_t bestSave = 0,
                     bool bestConfident = true,
                     uint8_t stationIdx = 0, uint8_t stationCount = 0,
                     bool isCheapest = false, int32_t vsBest = 0) {
  char buf[40], tmp[16];
  const uint16_t vc = uiVerdictColor(v->verdict);

  lcd.fillScreen(C_BLACK);

  // --- header: which station this price is for ---
  lcd.setTextSize(L_header.size);
  lcd.setTextColor(C_CYAN);
  lcd.setCursor(L_header.x, L_header.y);
  if (bestLabel && bestLabel[0]) {
    lcd.print(bestLabel);
    if (!bestConfident) lcd.print(F(" ?"));   // offset from very few samples
    if (stationCount > 1) {
      snprintf(buf, sizeof buf, " %u/%u",
               (unsigned)(stationIdx + 1), (unsigned)stationCount);
      lcd.setTextColor(C_DIM);
      lcd.print(buf);

      // Say outright whether this is the best price available, and if not, what
      // it costs to stop here instead. Scrolling a list of near-identical
      // numbers otherwise makes you do that subtraction in your head.
      if (isCheapest) {
        lcd.setTextColor(C_GREEN);
        lcd.print(F("  CHEAPEST"));
      } else if (vsBest > 0) {
        gp_fmt_cents(vsBest, tmp, sizeof tmp);
        snprintf(buf, sizeof buf, "  +%s vs best", tmp);
        lcd.setTextColor(C_AMBER);
        lcd.print(buf);
      }
    }
  } else {
    lcd.print(F("RICHMOND HILL"));
  }
  if (!online) {
    uiRightText("OFF", L_age.x, L_age.y, L_age.size, C_AMBER);
  } else if (in->age_minutes >= 0) {
    snprintf(buf, sizeof buf, "%ldh", (long)(in->age_minutes / 60));
    uiRightText(buf, L_age.x, L_age.y, L_age.size, C_GREY);
  }
  lcd.drawFastHLine(L_divider.x, L_divider.y, L_divider.w, C_DIM);

  // --- today's price, size 4: 6 glyphs * 24px = 144px ---
  // Green means "nothing tracked is cheaper right now". Carrying that on the
  // price itself makes it readable across the room, where the header tag isn't.
  gp_fmt_price(in->today, buf, sizeof buf);
  lcd.setTextSize(L_price.size);
  lcd.setTextColor(isCheapest && stationCount > 1 ? C_GREEN : C_WHITE);
  lcd.setCursor(L_price.x, L_price.y);
  lcd.print(buf);

  // --- right column: level % over a proportional bar ---
  if (v->level_pct >= 0) {
    snprintf(buf, sizeof buf, "LVL %ld%%", (long)v->level_pct);
    uiRightText(buf, L_level_text.x, L_level_text.y, L_level_text.size, C_GREY);

    lcd.drawRect(L_level_bar.x, L_level_bar.y, L_level_bar.w, L_level_bar.h,
                 C_DIM);
    int32_t fill = ((int32_t)(L_level_bar.w - 2) * v->level_pct) / 100;
    if (fill > 0)
      lcd.fillRect(L_level_bar.x + 1, L_level_bar.y + 1, (int16_t)fill,
                   L_level_bar.h - 2, vc);
  }

  // --- verdict bar, filled so it reads at a glance across the room ---
  lcd.fillRect(L_verdict_bar.x, L_verdict_bar.y, L_verdict_bar.w,
               L_verdict_bar.h, vc);
  const char *name = gp_verdict_name(v->verdict);
  lcd.setTextSize(L_verdict_text.size);
  lcd.setTextColor(uiOnVerdict(v->verdict));
  lcd.setCursor((LCD_W - uiTextW(name, L_verdict_text.size)) / 2,
                L_verdict_text.y);
  lcd.print(name);

  // --- reason: 21 chars max, size 2 = 252px of the 320 available ---
  gp_reason(in, v, buf, sizeof buf);
  lcd.setTextSize(L_reason.size);
  lcd.setTextColor(C_WHITE);
  lcd.setCursor(L_reason.x, L_reason.y);
  lcd.print(buf);

  // --- tank state, and the wait/jump number ---
  lcd.setTextSize(L_tank.size);
  lcd.setTextColor(C_GREY);
  lcd.setCursor(L_tank.x, L_tank.y);
  lcd.print(in->tank == TANK_FULL ? F("TANK FULL")
            : in->tank == TANK_LOW ? F("TANK LOW") : F("TANK HALF"));

  // Right of the tank row: the saving from driving here instead of the usual
  // station. This is the bigger number in practice — the spread across town
  // runs several times the day-to-day timing swing — so it wins the slot over
  // the wait countdown, which the reason line already spells out.
  if (bestSave > 0) {
    gp_fmt_cents(bestSave, tmp, sizeof tmp);
    snprintf(buf, sizeof buf, "SAVE %s", tmp);
    uiRightText(buf, L_savings.x, L_savings.y, L_savings.size, C_GREEN);
  } else if (bestSave < 0) {
    // Browsing a station dearer than your usual one — say so in red rather
    // than showing nothing, so cycling never looks like it stopped working.
    gp_fmt_cents(-bestSave, tmp, sizeof tmp);
    snprintf(buf, sizeof buf, "+%s", tmp);
    uiRightText(buf, L_savings.x, L_savings.y, L_savings.size, C_RED);
  } else if (stationCount > 0) {
    uiRightText("USUAL", L_savings.x, L_savings.y, L_savings.size, C_GREY);
  } else if (v->days_to_wait > 0) {
    gp_fmt_cents(v->save, tmp, sizeof tmp);
    snprintf(buf, sizeof buf, "%dd %s", (int)v->days_to_wait, tmp);
    uiRightText(buf, L_savings.x, L_savings.y, L_savings.size, C_GREY);
  } else if (v->tomorrow_jump != 0) {
    gp_fmt_cents(v->tomorrow_jump < 0 ? -v->tomorrow_jump : v->tomorrow_jump,
                 tmp, sizeof tmp);
    snprintf(buf, sizeof buf, "%c%s", v->tomorrow_jump > 0 ? '^' : 'v', tmp);
    uiRightText(buf, L_savings.x, L_savings.y, L_savings.size, C_GREY);
  }

  // --- sparkline ---
  uiSparkline(hist, histLen, L_sparkline.x, L_sparkline.y, L_sparkline.w,
              L_sparkline.h, L_sparkline.dot_r, C_GREY, vc);
}

#endif  // GP_UI_H
