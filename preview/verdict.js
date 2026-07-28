// Port of firmware/gasprices/verdict.h — the third implementation of the same
// engine, after the C one in the firmware and the Python one in the backend.
//
// It exists because the renderer draws strings the engine produces (the verdict
// name, the reason line, the formatted savings), so the preview cannot draw a
// frame without it. Like the other two, it is held to tests/vectors.csv: the
// expect_verdict / expect_level / expect_days / expect_save columns are checked
// on every run of preview/tests/compare_render.mjs, so it cannot drift.
//
// UNITS: every price is an integer in tenths of a cent per litre. $1.489/L is
// 1489. All division is truncating, matching C's integer division — hence the
// Math.trunc() everywhere. Do not "simplify" those away.

export const V = {
  STALE: 'STALE',
  FILL_NOW: 'FILL_NOW',
  WAIT: 'WAIT',
  GREAT: 'GREAT',
  EXPENSIVE: 'EXPENSIVE',
  NEUTRAL: 'NEUTRAL',
};

export const TANK = { FULL: 'FULL', HALF: 'HALF', LOW: 'LOW' };

export const DEFAULT_CONFIG = {
  save_threshold: 15,
  low_pct: 20,
  high_pct: 80,
  jump_level_cap: 60,
  max_age_minutes: 36 * 60,
  horizon: 5,
  patience_full_pct: 200,
  patience_half_pct: 100,
};

export const GP_PRED_MAX = 7;
export const GP_REASON_MAX_CHARS = 21;

export function verdictName(v) {
  switch (v) {
    case V.FILL_NOW: return 'FILL NOW';
    case V.WAIT: return 'WAIT';
    case V.GREAT: return 'GREAT';
    case V.EXPENSIVE: return 'EXPENSIVE';
    case V.NEUTRAL: return 'NEUTRAL';
    default: return 'STALE';
  }
}

// gp_fmt_cents: "1.5c" / "-0.4c"
export function fmtCents(tenths) {
  const sign = tenths < 0 ? '-' : '';
  const a = tenths < 0 ? -tenths : tenths;
  return `${sign}${Math.trunc(a / 10)}.${a % 10}c`;
}

// gp_fmt_price: "$1.489". snprintf's %03d, so the fraction is zero-padded.
export function fmtPrice(tenths) {
  const whole = Math.trunc(tenths / 1000);
  const frac = String(tenths % 1000).padStart(3, '0');
  return `$${whole}.${frac}`;
}

// gp_evaluate. Top-down, first match wins.
export function evaluate(input, cfg = DEFAULT_CONFIG) {
  const out = {
    verdict: V.NEUTRAL,
    level_pct: -1,
    tomorrow_jump: 0,
    days_to_wait: 0,
    save: 0,
    threshold: cfg.save_threshold,
    urgent_override: false,
  };

  if (input.age_minutes >= 0 && input.age_minutes > cfg.max_age_minutes) {
    out.verdict = V.STALE;
    return out;
  }

  // --- Signal 1: level. Where does today sit in the recent range? ---
  const span = input.window_hi - input.window_lo;
  if (span > 0) {
    let pct = Math.trunc(((input.today - input.window_lo) * 100) / span);
    if (pct < 0) pct = 0;         // today broke below the window: treat as floor
    if (pct > 100) pct = 100;
    out.level_pct = pct;
  }

  // --- Tank-adjusted threshold ---
  const multPct = input.tank === TANK.FULL ? cfg.patience_full_pct
                                           : cfg.patience_half_pct;
  out.threshold = Math.trunc((cfg.save_threshold * multPct) / 100);

  // --- Signal 2: direction. Is a cheaper day coming? ---
  let n = input.pred.length;
  if (n > cfg.horizon) n = cfg.horizon;
  if (n > GP_PRED_MAX) n = GP_PRED_MAX;

  if (n > 0) {
    out.tomorrow_jump = input.pred[0] - input.today;

    let futureMin = input.pred[0];
    for (let d = 1; d < n; d++) {
      if (input.pred[d] < futureMin) futureMin = input.pred[d];
    }
    out.save = input.today - futureMin;
    if (out.save < 0) out.save = 0;

    for (let d = 0; d < n; d++) {
      if (input.pred[d] <= input.today - out.threshold) {
        out.days_to_wait = d + 1;   // d == 0 means "tomorrow"
        break;
      }
    }
  }

  // --- Urgency override: running dry beats saving 3 cents. ---
  if (input.tank === TANK.LOW) {
    out.urgent_override = true;
    out.days_to_wait = 0;
    out.verdict = V.FILL_NOW;
    return out;
  }

  // --- Rules ---
  if (out.tomorrow_jump >= out.threshold &&
      out.level_pct >= 0 && out.level_pct <= cfg.jump_level_cap) {
    out.verdict = V.FILL_NOW;
  } else if (out.days_to_wait > 0) {
    out.verdict = V.WAIT;
  } else if (out.level_pct >= 0 && out.level_pct <= cfg.low_pct) {
    out.verdict = V.GREAT;
  } else if (out.level_pct >= cfg.high_pct) {
    out.verdict = V.EXPENSIVE;
  } else {
    out.verdict = V.NEUTRAL;
  }
  return out;
}

// gp_reason. HARD LIMIT GP_REASON_MAX_CHARS: anything longer runs off the panel.
export function reason(input, v) {
  if (v.urgent_override) return 'Tank low: fill anyway';
  switch (v.verdict) {
    case V.STALE:     return 'Stale - check wifi';
    case V.FILL_NOW:  return `Jumps ${fmtCents(v.tomorrow_jump)} tomorrow`;
    case V.WAIT:      return `Save ${fmtCents(v.save)} in ${v.days_to_wait} ` +
                             `day${v.days_to_wait === 1 ? '' : 's'}`;
    case V.GREAT:     return 'Bottom of range - go';
    case V.EXPENSIVE: return 'High, no dip coming';
    default:          return 'Fair price';
  }
}
