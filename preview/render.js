// render.js — a port of firmware/gasprices/ui.h that draws into a plain
// Uint16Array of RGB565, with no dependency on the DOM.
//
// That is deliberate. In the browser the buffer gets blitted with putImageData;
// in node it is compared byte-for-byte against the PPM files the C++ harness
// writes. So the golden diff needs no node-canvas, no puppeteer, no headless
// browser, and the whole project stays dependency-free.
//
// The primitives below are ports of Adafruit_GFX's own — Bresenham line,
// Bresenham circle, classic-font drawChar — matching tests/stubs/Adafruit_ST7789.h
// exactly. preview/tests/compare_render.mjs is what keeps them matching: if you
// change a primitive here without changing it there, it fails.
//
// KEEP IN STEP WITH ui.h. Draw order, colours and integer truncation all matter;
// the diff catches any divergence, so run it after editing either file.

import { FONT } from './glcdfont.js';
import { V, TANK, verdictName, fmtCents, fmtPrice, reason } from './verdict.js';

// RGB565, same values as the C_* defines in ui.h.
export const C = {
  BLACK: 0x0000,
  WHITE: 0xFFFF,
  GREY: 0x8410,
  DIM: 0x39E7,
  RED: 0xF800,
  GREEN: 0x07E0,
  AMBER: 0xFD20,
  PURPLE: 0x780F,
  CYAN: 0x07FF,
};

// ---------------------------------------------------------------------------
// The framebuffer. Same API surface ui.h uses, so the render function below can
// read as a transcription of it rather than a reinterpretation.
// ---------------------------------------------------------------------------
export class Framebuffer {
  constructor(w = 320, h = 172, { trace = false } = {}) {
    this.w = w;
    this.h = h;
    this.px = new Uint16Array(w * h);
    this.clipped = false;      // something was drawn outside the panel
    this.color = 0xFFFF;
    this.bg = 0xFFFF;
    this.size = 1;
    this.cx = 0;
    this.cy = 0;
    this.wrap = true;
    // Where each element's ink actually landed, for the drag editor's handles.
    // Off by default and pure bookkeeping when on: it must never change a pixel,
    // or the golden diff would be checking a different renderer than the browser
    // draws with.
    this.trace = trace ? {} : null;
    this._cur = null;
  }

  // Open a named element scope. Everything drawn until the next el() call is
  // attributed to it. render() names each element exactly once.
  el(name) {
    if (!this.trace) return;
    this._cur = name;
    if (name && !this.trace[name]) {
      this.trace[name] = { x0: Infinity, y0: Infinity, x1: -Infinity,
                           y1: -Infinity, clipped: false, ink: 0 };
    }
  }

  _mark(x, y, oob) {
    const b = this.trace[this._cur];
    if (x < b.x0) b.x0 = x;
    if (y < b.y0) b.y0 = y;
    if (x > b.x1) b.x1 = x;
    if (y > b.y1) b.y1 = y;
    b.ink++;
    if (oob) b.clipped = true;
  }

  fillScreen(c) { this.fillRect(0, 0, this.w, this.h, c); }

  // One argument sets background == foreground, which Adafruit_GFX treats as
  // "transparent": only set bits are drawn. ui.h relies on that for every
  // string it puts over the verdict bar.
  setTextColor(c, bg = c) { this.color = c; this.bg = bg; }
  setTextSize(s) { this.size = s < 1 ? 1 : s; }
  setTextWrap(w) { this.wrap = w; }
  setCursor(x, y) { this.cx = x; this.cy = y; }

  drawPixel(x, y, c) {
    const oob = x < 0 || y < 0 || x >= this.w || y >= this.h;
    if (this._cur) this._mark(x, y, oob);
    if (oob) { this.clipped = true; return; }
    this.px[y * this.w + x] = c;
  }

  fillRect(x, y, w, h, c) {
    for (let j = y; j < y + h; j++)
      for (let i = x; i < x + w; i++) this.drawPixel(i, j, c);
  }

  drawFastHLine(x, y, w, c) {
    for (let i = x; i < x + w; i++) this.drawPixel(i, y, c);
  }

  drawFastVLine(x, y, h, c) {
    for (let j = y; j < y + h; j++) this.drawPixel(x, j, c);
  }

  drawRect(x, y, w, h, c) {
    this.drawFastHLine(x, y, w, c);
    this.drawFastHLine(x, y + h - 1, w, c);
    this.drawFastVLine(x, y, h, c);
    this.drawFastVLine(x + w - 1, y, h, c);
  }

  // Adafruit_GFX::writeLine — Bresenham, with the steep/shallow swap.
  drawLine(x0, y0, x1, y1, c) {
    const steep = Math.abs(y1 - y0) > Math.abs(x1 - x0);
    if (steep) { [x0, y0] = [y0, x0]; [x1, y1] = [y1, x1]; }
    if (x0 > x1) { [x0, x1] = [x1, x0]; [y0, y1] = [y1, y0]; }

    const dx = x1 - x0, dy = Math.abs(y1 - y0);
    let err = Math.trunc(dx / 2);
    const ystep = y0 < y1 ? 1 : -1;

    for (; x0 <= x1; x0++) {
      if (steep) this.drawPixel(y0, x0, c);
      else this.drawPixel(x0, y0, c);
      err -= dy;
      if (err < 0) { y0 += ystep; err += dx; }
    }
  }

  // Adafruit_GFX::fillCircle + fillCircleHelper — a Bresenham circle. A radius
  // test disagrees with it at r=2, the size of the sparkline's dot.
  fillCircle(x0, y0, r, c) {
    this.drawFastVLine(x0, y0 - r, 2 * r + 1, c);
    this._fillCircleHelper(x0, y0, r, 3, 0, c);
  }

  _fillCircleHelper(x0, y0, r, corners, delta, c) {
    let f = 1 - r, ddF_x = 1, ddF_y = -2 * r;
    let x = 0, y = r, px = x, py = y;
    delta++;                                  // avoids some +1's in the loop

    while (x < y) {
      if (f >= 0) { y--; ddF_y += 2; f += ddF_y; }
      x++;
      ddF_x += 2;
      f += ddF_x;
      if (x < y + 1) {
        if (corners & 1) this.drawFastVLine(x0 + x, y0 - y, 2 * y + delta, c);
        if (corners & 2) this.drawFastVLine(x0 - x, y0 - y, 2 * y + delta, c);
      }
      if (y !== py) {
        if (corners & 1) this.drawFastVLine(x0 + py, y0 - px, 2 * px + delta, c);
        if (corners & 2) this.drawFastVLine(x0 - py, y0 - px, 2 * px + delta, c);
        py = y;
      }
      px = x;
    }
  }

  // Adafruit_GFX::drawChar, classic-font branch. Note the whole-glyph clip: a
  // glyph starting past the right edge draws nothing at all, so a string running
  // off the panel visibly stops instead of wrapping.
  drawChar(x, y, ch, color, bg, size) {
    if (x >= this.w || y >= this.h ||
        x + 6 * size - 1 < 0 || y + 8 * size - 1 < 0) return;

    let c = ch;
    if (c >= 176) c++;                        // classic charset behaviour

    for (let i = 0; i < 5; i++) {
      let line = FONT[c * 5 + i];
      for (let j = 0; j < 8; j++, line >>= 1) {
        if (line & 1) {
          if (size === 1) this.drawPixel(x + i, y + j, color);
          else this.fillRect(x + i * size, y + j * size, size, size, color);
        } else if (bg !== color) {
          if (size === 1) this.drawPixel(x + i, y + j, bg);
          else this.fillRect(x + i * size, y + j * size, size, size, bg);
        }
      }
    }
    if (bg !== color) {                       // opaque: fill the 6th column too
      if (size === 1) this.drawFastVLine(x + 5, y, 8, bg);
      else this.fillRect(x + 5 * size, y, size, 8 * size, bg);
    }
  }

  print(s) {
    for (let k = 0; k < s.length; k++) {
      if (this.wrap && this.cx + this.size * 6 > this.w) {
        this.cx = 0;
        this.cy += 8 * this.size;
      }
      this.drawChar(this.cx, this.cy, s.charCodeAt(k) & 0xFF,
                    this.color, this.bg, this.size);
      this.cx += 6 * this.size;
    }
  }

  // --- export helpers ---

  // RGB565 -> RGB888 by bit replication, the same expansion writePPM() uses in
  // the C++ stub. Each channel value maps to a distinct byte, so an RGB888
  // comparison is exactly an RGB565 comparison.
  toRGB() {
    const out = new Uint8Array(this.w * this.h * 3);
    for (let i = 0; i < this.px.length; i++) {
      const p = this.px[i];
      const r5 = (p >> 11) & 0x1F, g6 = (p >> 5) & 0x3F, b5 = p & 0x1F;
      out[i * 3] = (r5 << 3) | (r5 >> 2);
      out[i * 3 + 1] = (g6 << 2) | (g6 >> 4);
      out[i * 3 + 2] = (b5 << 3) | (b5 >> 2);
    }
    return out;
  }

  // For putImageData. Opaque alpha throughout; the panel has no transparency.
  toRGBA() {
    const out = new Uint8ClampedArray(this.w * this.h * 4);
    const rgb = this.toRGB();
    for (let i = 0; i < this.px.length; i++) {
      out[i * 4] = rgb[i * 3];
      out[i * 4 + 1] = rgb[i * 3 + 1];
      out[i * 4 + 2] = rgb[i * 3 + 2];
      out[i * 4 + 3] = 255;
    }
    return out;
  }
}

// ---------------------------------------------------------------------------
// ui.h's helpers
// ---------------------------------------------------------------------------

function verdictColor(v) {
  switch (v) {
    case V.FILL_NOW:
    case V.GREAT: return C.GREEN;
    case V.NEUTRAL: return C.AMBER;
    case V.WAIT:
    case V.EXPENSIVE: return C.RED;
    default: return C.PURPLE;
  }
}

// Text that stays legible on top of verdictColor().
function onVerdict(v) {
  const c = verdictColor(v);
  return (c === C.GREEN || c === C.AMBER) ? C.BLACK : C.WHITE;
}

export function textW(s, size) { return s.length * 6 * size; }

// uiRightText: draw s so its right edge lands on x=right.
function rightText(fb, s, right, y, size, color) {
  fb.setTextSize(size);
  fb.setTextColor(color);
  fb.setCursor(right - textW(s, size), y);
  fb.print(s);
}

// ---------------------------------------------------------------------------
// uiMessage — the boot and hard-failure screen.
// ---------------------------------------------------------------------------
export function renderMessage(title, detail, layout, opts = {}) {
  const L = layout.elements;
  const fb = new Framebuffer(layout.panel.w, layout.panel.h, opts);
  fb.setTextWrap(false);

  fb.fillScreen(C.BLACK);
  fb.el('message_title');
  fb.setTextSize(L.message_title.size);
  fb.setTextColor(C.WHITE);
  fb.setCursor(Math.trunc((fb.w - textW(title, L.message_title.size)) / 2),
               L.message_title.y);
  fb.print(title);
  if (detail) {
    fb.el('message_detail');
    fb.setTextSize(L.message_detail.size);
    fb.setTextColor(C.GREY);
    fb.setCursor(Math.trunc((fb.w - textW(detail, L.message_detail.size)) / 2),
                 L.message_detail.y);
    fb.print(detail);
  }
  fb.el(null);
  return fb;
}

// uiSparkline. hist[] is the rolling window in tenths of a cent; the last entry
// is today.
function sparkline(fb, hist, x, y, w, h, dotR, line, dot) {
  const n = hist.length;
  if (n < 2) return;

  let lo = hist[0], hi = hist[0];
  for (let i = 1; i < n; i++) {
    if (hist[i] < lo) lo = hist[i];
    if (hist[i] > hi) hi = hist[i];
  }
  let span = hi - lo;
  if (span <= 0) span = 1;

  // Inset by the dot radius on every side so the "today" dot at the last point
  // lands entirely on the panel instead of being clipped in half. Tied to dotR,
  // exactly as in ui.h.
  const x0 = x + dotR, x1 = x + w - dotR - 1;
  const y0 = y + dotR, y1 = y + h - dotR - 1;
  if (x1 <= x0 || y1 <= y0) return;

  let prevX = 0, prevY = 0;
  for (let i = 0; i < n; i++) {
    const px = x0 + Math.trunc((i * (x1 - x0)) / (n - 1));
    // Inverted: a high price plots high on screen.
    const py = y1 - Math.trunc(((hist[i] - lo) * (y1 - y0)) / span);
    if (i > 0) fb.drawLine(prevX, prevY, px, py, line);
    prevX = px;
    prevY = py;
  }
  // Fat dot on today so the eye lands on "where am I in this range".
  fb.fillCircle(prevX, prevY, dotR, dot);
}

// ---------------------------------------------------------------------------
// uiRender. Draw order matters — later calls paint over earlier ones.
// ---------------------------------------------------------------------------
export function render(state, layout, opts = {}) {
  const L = layout.elements;
  const fb = new Framebuffer(layout.panel.w, layout.panel.h, opts);
  fb.setTextWrap(false);

  const { input, v, hist, online } = state;
  const label = state.label || '';
  const bestSave = state.bestSave | 0;
  const bestConfident = state.bestConfident !== false;
  const stationIdx = state.stationIdx | 0;
  const stationCount = state.stationCount | 0;
  const isCheapest = !!state.isCheapest;
  const vsBest = state.vsBest | 0;

  const vc = verdictColor(v.verdict);

  fb.fillScreen(C.BLACK);

  // --- header: which station this price is for ---
  fb.el('header');
  fb.setTextSize(L.header.size);
  fb.setTextColor(C.CYAN);
  fb.setCursor(L.header.x, L.header.y);
  if (label) {
    fb.print(label);
    if (!bestConfident) fb.print(' ?');            // offset from very few samples
    if (stationCount > 1) {
      fb.setTextColor(C.DIM);
      fb.print(` ${stationIdx + 1}/${stationCount}`);

      // Say outright whether this is the best price available, and if not, what
      // it costs to stop here instead.
      if (isCheapest) {
        fb.setTextColor(C.GREEN);
        fb.print('  CHEAPEST');
      } else if (vsBest > 0) {
        fb.setTextColor(C.AMBER);
        fb.print(`  +${fmtCents(vsBest)} vs best`);
      }
    }
  } else {
    fb.print('RICHMOND HILL');
  }
  fb.el('age');
  if (!online) {
    rightText(fb, 'OFF', L.age.x, L.age.y, L.age.size, C.AMBER);
  } else if (input.age_minutes >= 0) {
    rightText(fb, `${Math.trunc(input.age_minutes / 60)}h`,
              L.age.x, L.age.y, L.age.size, C.GREY);
  }
  fb.el('divider');
  fb.drawFastHLine(L.divider.x, L.divider.y, L.divider.w, C.DIM);

  // --- today's price ---
  // Green means "nothing tracked is cheaper right now".
  fb.el('price');
  fb.setTextSize(L.price.size);
  fb.setTextColor(isCheapest && stationCount > 1 ? C.GREEN : C.WHITE);
  fb.setCursor(L.price.x, L.price.y);
  fb.print(fmtPrice(input.today));

  // --- right column: level % over a proportional bar ---
  if (v.level_pct >= 0) {
    fb.el('level_text');
    rightText(fb, `LVL ${v.level_pct}%`,
              L.level_text.x, L.level_text.y, L.level_text.size, C.GREY);

    fb.el('level_bar');
    const b = L.level_bar;
    fb.drawRect(b.x, b.y, b.w, b.h, C.DIM);
    const fill = Math.trunc(((b.w - 2) * v.level_pct) / 100);
    if (fill > 0) fb.fillRect(b.x + 1, b.y + 1, fill, b.h - 2, vc);
  }

  // --- verdict bar, filled so it reads at a glance across the room ---
  fb.el('verdict_bar');
  const bar = L.verdict_bar;
  fb.fillRect(bar.x, bar.y, bar.w, bar.h, vc);
  fb.el('verdict_text');
  const name = verdictName(v.verdict);
  fb.setTextSize(L.verdict_text.size);
  fb.setTextColor(onVerdict(v.verdict));
  fb.setCursor(Math.trunc((fb.w - textW(name, L.verdict_text.size)) / 2),
               L.verdict_text.y);
  fb.print(name);

  // --- reason: 21 chars max ---
  fb.el('reason');
  fb.setTextSize(L.reason.size);
  fb.setTextColor(C.WHITE);
  fb.setCursor(L.reason.x, L.reason.y);
  fb.print(reason(input, v));

  // --- tank state ---
  fb.el('tank');
  fb.setTextSize(L.tank.size);
  fb.setTextColor(C.GREY);
  fb.setCursor(L.tank.x, L.tank.y);
  fb.print(input.tank === TANK.FULL ? 'TANK FULL'
           : input.tank === TANK.LOW ? 'TANK LOW' : 'TANK HALF');

  // Right of the tank row: the saving from driving here instead of the usual
  // station. That is the bigger number in practice, so it wins the slot over the
  // wait countdown, which the reason line already spells out.
  fb.el('savings');
  const sv = L.savings;
  if (bestSave > 0) {
    rightText(fb, `SAVE ${fmtCents(bestSave)}`, sv.x, sv.y, sv.size, C.GREEN);
  } else if (bestSave < 0) {
    // Browsing a station dearer than your usual one — say so in red rather than
    // showing nothing, so cycling never looks like it stopped working.
    rightText(fb, `+${fmtCents(-bestSave)}`, sv.x, sv.y, sv.size, C.RED);
  } else if (stationCount > 0) {
    // Zero saving means you are looking at the station savings are measured
    // against — your home station (backend/stations.csv, role `home`).
    rightText(fb, 'HOME', sv.x, sv.y, sv.size, C.GREY);
  } else if (v.days_to_wait > 0) {
    rightText(fb, `${v.days_to_wait}d ${fmtCents(v.save)}`,
              sv.x, sv.y, sv.size, C.GREY);
  } else if (v.tomorrow_jump !== 0) {
    const mag = v.tomorrow_jump < 0 ? -v.tomorrow_jump : v.tomorrow_jump;
    rightText(fb, `${v.tomorrow_jump > 0 ? '^' : 'v'}${fmtCents(mag)}`,
              sv.x, sv.y, sv.size, C.GREY);
  }

  // --- sparkline ---
  fb.el('sparkline');
  const sp = L.sparkline;
  sparkline(fb, hist, sp.x, sp.y, sp.w, sp.h, sp.dot_r, C.GREY, vc);

  fb.el(null);
  return fb;
}
