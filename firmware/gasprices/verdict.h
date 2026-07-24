// verdict.h — the decision engine. Pure C, no Arduino dependencies, so the same
// file compiles into the firmware and into tests/test_verdict.cpp on your laptop.
//
// UNITS: every price is an int32_t in *tenths of a cent per litre*.
//   $1.489/L  -> 1489
//   1.5 c/L   -> 15
// Gas is quoted to exactly 3 decimals, so this is lossless and dodges both
// float rounding in comparisons and the ESP32-C6's lack of a hardware FPU.
#ifndef GP_VERDICT_H
#define GP_VERDICT_H

#include <stdint.h>
#include <stdio.h>
#include <stdbool.h>

#define GP_PRED_MAX 7

typedef enum {
  V_STALE = 0,   // data too old to trust
  V_FILL_NOW,    // cheap-ish now and a jump is coming
  V_WAIT,        // a meaningfully cheaper day is within the horizon
  V_GREAT,       // bottom of the recent range
  V_EXPENSIVE,   // top of the range, but no dip coming
  V_NEUTRAL      // fair price, no signal
} Verdict;

typedef enum { TANK_FULL = 0, TANK_HALF = 1, TANK_LOW = 2 } TankState;

typedef struct {
  int32_t   today;                 // today's local pump price
  int32_t   pred[GP_PRED_MAX];     // pred[0] = tomorrow, pred[1] = day after, ...
  uint8_t   pred_len;
  int32_t   window_lo;             // min over the rolling history window
  int32_t   window_hi;             // max over the rolling history window
  int32_t   age_minutes;           // how old the JSON is (-1 = unknown / no clock)
  TankState tank;
} GpInput;

typedef struct {
  int32_t save_threshold;   // min saving worth a special trip, default 15 (1.5 c/L)
  int32_t low_pct;          // <= this level% is GREAT           (default 20)
  int32_t high_pct;         // >= this level% is EXPENSIVE       (default 80)
  int32_t jump_level_cap;   // FILL_NOW only if level% <= this   (default 60)
  int32_t max_age_minutes;  // beyond this the data is STALE     (default 2160 = 36 h)
  uint8_t horizon;          // how many predicted days to search (default 5)

  // Patience multiplier by tank state, in percent. A full tank can afford to be
  // fussy; a low tank shouldn't be. TANK_LOW is special-cased (hard override).
  int32_t patience_full_pct;  // default 200 -> needs 3.0 c/L to bother
  int32_t patience_half_pct;  // default 100 -> the base threshold
} GpConfig;

typedef struct {
  Verdict verdict;
  int32_t level_pct;        // 0 = cheapest in window, 100 = priciest. -1 if unknown.
  int32_t tomorrow_jump;    // pred[0] - today (positive = it gets worse tomorrow)
  uint8_t days_to_wait;     // 1 = tomorrow, 0 = don't wait
  int32_t save;             // today - min(pred[0..horizon-1]), clamped at 0
  int32_t threshold;        // the tank-adjusted threshold actually applied
  bool    urgent_override;  // true if TANK_LOW forced the answer
} GpVerdict;

static inline GpConfig gp_default_config(void) {
  GpConfig c;
  c.save_threshold    = 15;
  c.low_pct           = 20;
  c.high_pct          = 80;
  c.jump_level_cap    = 60;
  c.max_age_minutes   = 36 * 60;
  c.horizon           = 5;
  c.patience_full_pct = 200;
  c.patience_half_pct = 100;
  return c;
}

static inline const char *gp_verdict_name(Verdict v) {
  switch (v) {
    case V_FILL_NOW:  return "FILL NOW";
    case V_WAIT:      return "WAIT";
    case V_GREAT:     return "GREAT";
    case V_EXPENSIVE: return "EXPENSIVE";
    case V_NEUTRAL:   return "NEUTRAL";
    default:          return "STALE";
  }
}

// 0xRRGGBB for the status LED. Green = go, yellow = meh, red = hold off.
static inline uint32_t gp_verdict_color(Verdict v) {
  switch (v) {
    case V_FILL_NOW:  return 0x00FF00;
    case V_GREAT:     return 0x00FF00;
    case V_NEUTRAL:   return 0x804000;  // dim amber
    case V_WAIT:      return 0xFF0000;
    case V_EXPENSIVE: return 0xFF0000;
    default:          return 0x100010;  // faint purple = stale
  }
}

// Format tenths-of-a-cent as "1.5c" / "-0.4c" into buf.
static inline void gp_fmt_cents(int32_t tenths, char *buf, size_t n) {
  const char *sign = tenths < 0 ? "-" : "";
  int32_t a = tenths < 0 ? -tenths : tenths;
  snprintf(buf, n, "%s%d.%dc", sign, (int)(a / 10), (int)(a % 10));
}

// Format tenths-of-a-cent-per-litre as a pump price "$1.489".
static inline void gp_fmt_price(int32_t tenths, char *buf, size_t n) {
  snprintf(buf, n, "$%d.%03d", (int)(tenths / 1000), (int)(tenths % 1000));
}

// ---------------------------------------------------------------------------
// The engine. Top-down, first match wins.
// ---------------------------------------------------------------------------
static inline void gp_evaluate(const GpInput *in, const GpConfig *cfg, GpVerdict *out) {
  out->level_pct       = -1;
  out->tomorrow_jump   = 0;
  out->days_to_wait    = 0;
  out->save            = 0;
  out->threshold       = cfg->save_threshold;
  out->urgent_override = false;

  if (in->age_minutes >= 0 && in->age_minutes > cfg->max_age_minutes) {
    out->verdict = V_STALE;
    return;
  }

  // --- Signal 1: level. Where does today sit in the recent range? ---
  int32_t span = in->window_hi - in->window_lo;
  if (span > 0) {
    int32_t pct = ((in->today - in->window_lo) * 100) / span;
    if (pct < 0)   pct = 0;      // today broke below the window: treat as the floor
    if (pct > 100) pct = 100;
    out->level_pct = pct;
  }

  // --- Tank-adjusted threshold ---
  int32_t mult_pct = (in->tank == TANK_FULL) ? cfg->patience_full_pct
                                             : cfg->patience_half_pct;
  out->threshold = (cfg->save_threshold * mult_pct) / 100;

  // --- Signal 2: direction. Is a cheaper day coming? ---
  uint8_t n = in->pred_len;
  if (n > cfg->horizon)   n = cfg->horizon;
  if (n > GP_PRED_MAX)    n = GP_PRED_MAX;

  if (n > 0) {
    out->tomorrow_jump = in->pred[0] - in->today;

    int32_t future_min = in->pred[0];
    for (uint8_t d = 1; d < n; d++) {
      if (in->pred[d] < future_min) future_min = in->pred[d];
    }
    out->save = in->today - future_min;
    if (out->save < 0) out->save = 0;

    for (uint8_t d = 0; d < n; d++) {
      if (in->pred[d] <= in->today - out->threshold) {
        out->days_to_wait = (uint8_t)(d + 1);   // d == 0 means "tomorrow"
        break;
      }
    }
  }

  // --- Urgency override: running dry beats saving 3 cents. ---
  if (in->tank == TANK_LOW) {
    out->urgent_override = true;
    out->days_to_wait    = 0;
    out->verdict         = V_FILL_NOW;
    return;
  }

  // --- Rules ---
  if (out->tomorrow_jump >= out->threshold &&
      out->level_pct >= 0 && out->level_pct <= cfg->jump_level_cap) {
    out->verdict = V_FILL_NOW;
  } else if (out->days_to_wait > 0) {
    out->verdict = V_WAIT;
  } else if (out->level_pct >= 0 && out->level_pct <= cfg->low_pct) {
    out->verdict = V_GREAT;
  } else if (out->level_pct >= cfg->high_pct) {
    out->verdict = V_EXPENSIVE;
  } else {
    out->verdict = V_NEUTRAL;
  }
}

// One line of plain English for the display. buf should be >= 40 bytes.
// HARD LIMIT: 21 characters. That is one line of the 128px panel at text size 1
// (128 / 6px per glyph). tests/test_verdict.cpp fails the build if any string
// here grows past it, because the overflow silently vanishes off-screen.
#define GP_REASON_MAX_CHARS 21

static inline void gp_reason(const GpInput *in, const GpVerdict *v, char *buf, size_t n) {
  char c1[12];
  (void)in;

  if (v->urgent_override) {
    snprintf(buf, n, "Tank low: fill anyway");
    return;
  }
  switch (v->verdict) {
    case V_STALE:
      snprintf(buf, n, "Stale - check wifi");
      break;
    case V_FILL_NOW:
      gp_fmt_cents(v->tomorrow_jump, c1, sizeof c1);
      snprintf(buf, n, "Jumps %s tomorrow", c1);
      break;
    case V_WAIT:
      gp_fmt_cents(v->save, c1, sizeof c1);
      snprintf(buf, n, "Save %s in %d day%s", c1, (int)v->days_to_wait,
               v->days_to_wait == 1 ? "" : "s");
      break;
    case V_GREAT:
      snprintf(buf, n, "Bottom of range - go");
      break;
    case V_EXPENSIVE:
      snprintf(buf, n, "High, no dip coming");
      break;
    default:
      snprintf(buf, n, "Fair price");
      break;
  }
}

#endif  // GP_VERDICT_H
