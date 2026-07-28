// states.js — tests/vectors.csv -> the argument lists uiRender() takes.
//
// The same 14 cases the firmware engine test, the backend engine test and the
// ASCII layout harness all run, so the preview shows the states that are already
// under test rather than a separate set of made-up ones.

import { evaluate, TANK } from './verdict.js';

export const HIST_LEN = 14;

// Mirror of synth_history() in tests/test_ui.cpp. A plausible 14-day window
// derived from the case's own range, so vectors.csv needs no history column.
// The integer division is C's, hence Math.trunc — change it here and the pixel
// diff against the C++ golden images fails, which is the point.
export function synthHistory(input, n = HIST_LEN) {
  const lo = input.window_lo, hi = input.window_hi;
  const hist = new Array(n);
  for (let i = 0; i < n; i++) {
    hist[i] = (i % 2)
      ? lo + Math.trunc(((hi - lo) * (i + 2)) / 18)
      : hi - Math.trunc(((hi - lo) * i) / 20);
  }
  hist[n - 1] = input.today;
  return hist;
}

function parseTank(s) {
  return s === 'LOW' ? TANK.LOW : s === 'FULL' ? TANK.FULL : TANK.HALF;
}

// Column indices. 0..10 are the engine contract shared with the two verdict
// tests; 11..17 are the display-only arguments.
const COL = {
  name: 0, today: 1, pred: 2, window_lo: 3, window_hi: 4, age_minutes: 5,
  tank: 6, expect_verdict: 7, expect_level: 8, expect_days: 9, expect_save: 10,
  ui_label: 11, ui_station_idx: 12, ui_station_count: 13, ui_is_cheapest: 14,
  ui_vs_best: 15, ui_best_save: 16, ui_best_confident: 17,
};
const MIN_COLS = 18;

export function parseVectors(text) {
  const states = [];
  for (const raw of text.split('\n')) {
    const line = raw.trim();
    if (!line || line.startsWith('#') || line.startsWith('name,')) continue;
    const c = line.split(',');
    if (c.length < MIN_COLS) continue;

    const input = {
      today: parseInt(c[COL.today], 10),
      pred: c[COL.pred] === '-' ? []
            : c[COL.pred].split('|').map((p) => parseInt(p, 10)).slice(0, 7),
      window_lo: parseInt(c[COL.window_lo], 10),
      window_hi: parseInt(c[COL.window_hi], 10),
      age_minutes: parseInt(c[COL.age_minutes], 10),
      tank: parseTank(c[COL.tank]),
    };
    const label = c[COL.ui_label] === '-' ? '' : c[COL.ui_label];

    states.push({
      name: c[COL.name],
      input,
      v: evaluate(input),
      hist: synthHistory(input),
      online: input.age_minutes >= 0,
      label,
      stationIdx: parseInt(c[COL.ui_station_idx], 10),
      stationCount: parseInt(c[COL.ui_station_count], 10),
      isCheapest: c[COL.ui_is_cheapest] !== '0',
      vsBest: parseInt(c[COL.ui_vs_best], 10),
      bestSave: parseInt(c[COL.ui_best_save], 10),
      bestConfident: c[COL.ui_best_confident] !== '0',
      // What the engine is contracted to produce. compare_render.mjs checks the
      // JS port against these before it looks at a single pixel.
      expect: {
        verdict: c[COL.expect_verdict],
        level: parseInt(c[COL.expect_level], 10),
        days: parseInt(c[COL.expect_days], 10),
        save: parseInt(c[COL.expect_save], 10),
      },
    });
  }
  return states;
}
