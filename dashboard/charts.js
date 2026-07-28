/* SVG chart primitives for the dashboard.
 *
 * Written rather than imported, for the same reason preview/render.js draws its
 * own pixels: this repo has no build step and no node_modules, and the charts
 * here are lines, scatters, bars and a heatmap — a few dozen lines each.
 *
 * THE ONE RULE: every horizontal axis is a real time scale.
 *
 * backend/history.csv is weekly for its first sixty rows and daily after that.
 * Plotting by array index would draw seven months and three weeks at the same
 * width, which is the exact bug CLAUDE.md records twice. `timeScale` below takes
 * dates, not positions, and nothing in this file accepts an index.
 *
 * Colours come from the ST7789 panel (firmware/gasprices/ui.h), converted
 * RGB565 -> RGB888 by the same bit-replication preview/render.js uses, so the
 * dashboard and the device agree on what green means.
 */

export const C = {
  black: '#000000',
  white: '#ffffff',
  grey: '#848284',   // C_GREY   0x8410
  dim: '#393c39',    // C_DIM    0x39e7
  red: '#ff0000',    // C_RED    0xf800
  green: '#00ff00',  // C_GREEN  0x07e0
  amber: '#ffa600',  // C_AMBER  0xfd20
  purple: '#7b007b', // C_PURPLE 0x780f
  cyan: '#00ffff',   // C_CYAN   0x07ff
};

/* Verdict -> colour, mirroring uiVerdictColor() in ui.h. If these ever
 * disagree, the dashboard is telling you a different story than the panel. */
export function verdictColor(v) {
  switch (v) {
    case 'FILL_NOW':
    case 'GREAT': return C.green;
    case 'NEUTRAL': return C.amber;
    case 'WAIT':
    case 'EXPENSIVE': return C.red;
    default: return C.purple;
  }
}

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const day = (d) => Date.parse(d + 'T00:00:00Z');
const fmtDay = (ms) => new Date(ms).toISOString().slice(0, 10);

/* --- scales -------------------------------------------------------------- */

export function timeScale(dates, x0, x1) {
  const ts = dates.map(day).filter(Number.isFinite);
  let lo = Math.min(...ts); let hi = Math.max(...ts);
  if (!Number.isFinite(lo)) { lo = 0; hi = 1; }
  if (lo === hi) { lo -= 86400000; hi += 86400000; }
  const f = (d) => x0 + ((day(d) - lo) / (hi - lo)) * (x1 - x0);
  f.lo = lo; f.hi = hi; f.domain = [fmtDay(lo), fmtDay(hi)];
  return f;
}

export function valueScale(values, y0, y1, { pad = 0.08, zero = false } = {}) {
  const vs = values.filter(Number.isFinite);
  let lo = Math.min(...vs); let hi = Math.max(...vs);
  if (!Number.isFinite(lo)) { lo = 0; hi = 1; }
  if (zero) lo = Math.min(0, lo);
  const span = (hi - lo) || Math.abs(hi) || 1;
  lo -= span * pad; hi += span * pad;
  const f = (v) => y0 + (1 - (v - lo) / (hi - lo)) * (y1 - y0);
  f.lo = lo; f.hi = hi;
  return f;
}

/* --- axes ---------------------------------------------------------------- */

function timeTicks(x, n = 5) {
  const out = [];
  for (let i = 0; i <= n; i++) {
    const ms = x.lo + ((x.hi - x.lo) * i) / n;
    out.push({ px: x(fmtDay(ms)), label: fmtDay(ms).slice(2) });
  }
  return out;
}

function valueTicks(y, n, fmt) {
  const out = [];
  for (let i = 0; i <= n; i++) {
    const v = y.lo + ((y.hi - y.lo) * i) / n;
    out.push({ py: y(v), label: fmt(v) });
  }
  return out;
}

function axes(x, y, box, fmt, { ygrid = 4 } = {}) {
  const { l, r, t, b } = box;
  let s = '';
  for (const tk of valueTicks(y, ygrid, fmt)) {
    s += `<line x1="${l}" y1="${tk.py.toFixed(1)}" x2="${r}" y2="${tk.py.toFixed(1)}" stroke="${C.dim}" stroke-width="1"/>`;
    s += `<text x="${l - 6}" y="${(tk.py + 3.5).toFixed(1)}" text-anchor="end" class="tick">${esc(tk.label)}</text>`;
  }
  for (const tk of timeTicks(x)) {
    s += `<text x="${tk.px.toFixed(1)}" y="${b + 14}" text-anchor="middle" class="tick">${esc(tk.label)}</text>`;
  }
  s += `<line x1="${l}" y1="${t}" x2="${l}" y2="${b}" stroke="${C.grey}" stroke-width="1"/>`;
  s += `<line x1="${l}" y1="${b}" x2="${r}" y2="${b}" stroke="${C.grey}" stroke-width="1"/>`;
  return s;
}

function path(points, x, y) {
  return points
    .filter((p) => Number.isFinite(p.v))
    .map((p, i) => `${i ? 'L' : 'M'}${x(p.date).toFixed(1)},${y(p.v).toFixed(1)}`)
    .join('');
}

/* --- line chart ----------------------------------------------------------- */

/* series: [{ name, points:[{date,v,src}], color, dash, width, dots }] */
export function lineChart(series, opts = {}) {
  const w = opts.width || 900;
  const h = opts.height || 260;
  const box = { l: 58, r: w - 12, t: 12, b: h - 24 };
  const live = series.filter((s) => s.points && s.points.length);
  if (!live.length) return empty(w, h, opts.emptyText || 'no data');

  const x = timeScale(live.flatMap((s) => s.points.map((p) => p.date)), box.l, box.r);
  const y = valueScale(live.flatMap((s) => s.points.map((p) => p.v)),
    box.t, box.b, { pad: opts.pad ?? 0.08 });
  const fmt = opts.format || ((v) => v.toFixed(3));

  let s = `<svg viewBox="0 0 ${w} ${h}" class="chart" preserveAspectRatio="none">`;
  s += axes(x, y, box, fmt, opts);

  for (const ser of live) {
    s += `<path d="${path(ser.points, x, y)}" fill="none" stroke="${ser.color}"
      stroke-width="${ser.width || 1.5}" ${ser.dash ? `stroke-dasharray="${ser.dash}"` : ''}
      stroke-linejoin="round"/>`;
    if (ser.dots) {
      for (const p of ser.points) {
        if (!Number.isFinite(p.v)) continue;
        // A logged pump price is a fact; a survey number is a regional average;
        // a modelled one is a guess. Same line, different weight of evidence.
        const fill = p.src === 'logged' ? C.cyan
          : p.src === 'survey' ? ser.color : C.dim;
        const r = p.src === 'logged' ? 3.2 : 2;
        s += `<circle cx="${x(p.date).toFixed(1)}" cy="${y(p.v).toFixed(1)}" r="${r}"
          fill="${fill}" stroke="${C.black}" stroke-width="0.5"><title>${esc(p.date)}  ${fmt(p.v)}${p.src ? `  (${p.src})` : ''}</title></circle>`;
      }
    }
  }
  s += '</svg>';
  return s;
}

/* --- scatter with a rolling band ------------------------------------------ */

export function scatterChart(points, line, opts = {}) {
  const w = opts.width || 900;
  const h = opts.height || 240;
  const box = { l: 58, r: w - 12, t: 12, b: h - 24 };
  if (!points.length) return empty(w, h, opts.emptyText || 'no observations');

  const all = points.map((p) => p.v).concat(line.map((p) => p.v));
  const x = timeScale(points.concat(line).map((p) => p.date), box.l, box.r);
  const y = valueScale(all, box.t, box.b, { pad: 0.12 });
  const fmt = opts.format || ((v) => (v * 100).toFixed(1));

  let s = `<svg viewBox="0 0 ${w} ${h}" class="chart" preserveAspectRatio="none">`;
  s += axes(x, y, box, fmt, opts);
  for (const p of points) {
    s += `<circle cx="${x(p.date).toFixed(1)}" cy="${y(p.v).toFixed(1)}" r="2.4"
      fill="${C.grey}" opacity="0.75"><title>${esc(p.date)}  ${fmt(p.v)}</title></circle>`;
  }
  if (line.length) {
    s += `<path d="${path(line, x, y)}" fill="none" stroke="${C.amber}" stroke-width="2"/>`;
  }
  s += '</svg>';
  return s;
}

/* --- grouped bars --------------------------------------------------------- */

/* groups: [{ label, bars:[{name,v,color}] }] */
export function barChart(groups, opts = {}) {
  const w = opts.width || 900;
  const h = opts.height || 220;
  const box = { l: 58, r: w - 12, t: 12, b: h - 30 };
  if (!groups.length) return empty(w, h, opts.emptyText || 'nothing to score yet');

  const vals = groups.flatMap((g) => g.bars.map((b) => b.v));
  const y = valueScale(vals, box.t, box.b, { pad: 0.12, zero: true });
  const fmt = opts.format || ((v) => v.toFixed(2));
  const gw = (box.r - box.l) / groups.length;

  let s = `<svg viewBox="0 0 ${w} ${h}" class="chart" preserveAspectRatio="none">`;
  for (const tk of valueTicks(y, 4, fmt)) {
    s += `<line x1="${box.l}" y1="${tk.py.toFixed(1)}" x2="${box.r}" y2="${tk.py.toFixed(1)}" stroke="${C.dim}"/>`;
    s += `<text x="${box.l - 6}" y="${(tk.py + 3.5).toFixed(1)}" text-anchor="end" class="tick">${esc(tk.label)}</text>`;
  }
  const zero = y(0);
  s += `<line x1="${box.l}" y1="${zero.toFixed(1)}" x2="${box.r}" y2="${zero.toFixed(1)}" stroke="${C.grey}"/>`;

  groups.forEach((g, gi) => {
    const bw = (gw * 0.72) / g.bars.length;
    const x0 = box.l + gw * gi + gw * 0.14;
    g.bars.forEach((b, bi) => {
      const yv = y(b.v);
      const top = Math.min(yv, zero);
      const hh = Math.max(1, Math.abs(yv - zero));
      s += `<rect x="${(x0 + bw * bi).toFixed(1)}" y="${top.toFixed(1)}"
        width="${(bw - 2).toFixed(1)}" height="${hh.toFixed(1)}" fill="${b.color}"
        opacity="0.85"><title>${esc(b.name)}: ${fmt(b.v)}</title></rect>`;
    });
    s += `<text x="${(box.l + gw * gi + gw / 2).toFixed(1)}" y="${box.b + 16}"
      text-anchor="middle" class="tick">${esc(g.label)}</text>`;
  });
  s += '</svg>';
  return s;
}

/* --- heatmap -------------------------------------------------------------- */

export function heatmap(grid, rows, cols, opts = {}) {
  const w = opts.width || 900;
  const h = opts.height || 240;
  const box = { l: 58, r: w - 12, t: 24, b: h - 24 };
  const flat = grid.flat().filter(Number.isFinite);
  if (!flat.length) return empty(w, h, 'no sweep');
  const hi = Math.max(...flat.map(Math.abs)) || 1;
  const cw = (box.r - box.l) / cols.length;
  const ch = (box.b - box.t) / rows.length;
  const best = Math.max(...flat);

  let s = `<svg viewBox="0 0 ${w} ${h}" class="chart" preserveAspectRatio="none">`;
  cols.forEach((c, ci) => {
    s += `<text x="${(box.l + cw * ci + cw / 2).toFixed(1)}" y="${box.t - 8}"
      text-anchor="middle" class="tick">${esc(c)}</text>`;
  });
  rows.forEach((r, ri) => {
    s += `<text x="${box.l - 6}" y="${(box.t + ch * ri + ch / 2 + 3.5).toFixed(1)}"
      text-anchor="end" class="tick">${esc(r)}</text>`;
    cols.forEach((c, ci) => {
      const v = grid[ri][ci];
      // Green = beats the baseline, red = loses to it. Same semantics as the
      // panel, so a red cell reads as "don't" without a legend.
      const a = Math.min(1, Math.abs(v) / hi) * 0.85 + 0.05;
      const fill = v >= 0 ? C.green : C.red;
      s += `<rect x="${(box.l + cw * ci).toFixed(1)}" y="${(box.t + ch * ri).toFixed(1)}"
        width="${(cw - 1).toFixed(1)}" height="${(ch - 1).toFixed(1)}"
        fill="${fill}" opacity="${a.toFixed(2)}"
        ${v === best ? `stroke="${C.white}" stroke-width="1.5"` : ''}>
        <title>threshold ${esc(r)}, horizon ${esc(c)}: ${v.toFixed(2)} c/L</title></rect>`;
      s += `<text x="${(box.l + cw * ci + cw / 2).toFixed(1)}"
        y="${(box.t + ch * ri + ch / 2 + 3.5).toFixed(1)}" text-anchor="middle"
        class="cell">${v.toFixed(2)}</text>`;
    });
  });
  s += '</svg>';
  return s;
}

/* --- coverage strip ------------------------------------------------------- */

/* One column per row of history.csv, one band per column of it. The honest
 * counterweight to every other chart: 64 rows, two of them logged prices. */
export function coverageStrip(rows, fields, opts = {}) {
  const w = opts.width || 900;
  const rowH = 14;
  const h = fields.length * rowH + 30;
  const box = { l: 120, r: w - 12, t: 8 };
  if (!rows.length) return empty(w, h, 'no history');

  const x = timeScale(rows.map((r) => r.date), box.l, box.r);
  const cw = Math.max(1.5, (box.r - box.l) / Math.max(rows.length, 1) * 0.8);

  let s = `<svg viewBox="0 0 ${w} ${h}" class="chart" preserveAspectRatio="none">`;
  fields.forEach((f, fi) => {
    const yy = box.t + fi * rowH;
    s += `<text x="${box.l - 8}" y="${yy + 10}" text-anchor="end" class="tick">${esc(f)}</text>`;
    s += `<line x1="${box.l}" y1="${yy + 6}" x2="${box.r}" y2="${yy + 6}" stroke="${C.dim}"/>`;
    for (const r of rows) {
      if (!r.filled[fi]) continue;
      const isReal = f === 'retail_actual' || f === 'retail_survey';
      s += `<rect x="${(x(r.date) - cw / 2).toFixed(1)}" y="${yy + 1}"
        width="${cw.toFixed(1)}" height="10" fill="${isReal ? C.cyan : C.grey}"
        opacity="${isReal ? 0.95 : 0.5}"><title>${esc(r.date)} ${esc(f)}</title></rect>`;
    }
  });
  for (const tk of timeTicks(x, 6)) {
    s += `<text x="${tk.px.toFixed(1)}" y="${h - 6}" text-anchor="middle" class="tick">${esc(tk.label)}</text>`;
  }
  s += '</svg>';
  return s;
}

/* --- verdict timeline ----------------------------------------------------- */

/* One tick per day, coloured by what the device would have said. */
export function verdictStrip(days, opts = {}) {
  const w = opts.width || 900;
  const h = opts.height || 96;
  const box = { l: 58, r: w - 12, t: 10, b: h - 26 };
  if (!days.length) return empty(w, h, 'no verdicts');

  const x = timeScale(days.map((d) => d.date), box.l, box.r);
  const cw = Math.max(1.5, ((box.r - box.l) / days.length) * 0.85);
  const y = valueScale([0, 100], box.t, box.b, { pad: 0.02 });

  let s = `<svg viewBox="0 0 ${w} ${h}" class="chart" preserveAspectRatio="none">`;
  for (const lvl of [20, 80]) {
    s += `<line x1="${box.l}" y1="${y(lvl).toFixed(1)}" x2="${box.r}" y2="${y(lvl).toFixed(1)}"
      stroke="${C.dim}" stroke-dasharray="3 3"/>`;
    s += `<text x="${box.l - 6}" y="${(y(lvl) + 3.5).toFixed(1)}" text-anchor="end" class="tick">${lvl}%</text>`;
  }
  for (const d of days) {
    const col = verdictColor(d.verdict);
    const lvl = d.level_pct >= 0 ? d.level_pct : 50;
    s += `<rect x="${(x(d.date) - cw / 2).toFixed(1)}" y="${box.t}"
      width="${cw.toFixed(1)}" height="${(box.b - box.t).toFixed(1)}"
      fill="${col}" opacity="0.22"/>`;
    s += `<rect x="${(x(d.date) - cw / 2).toFixed(1)}" y="${(y(lvl) - 1.5).toFixed(1)}"
      width="${cw.toFixed(1)}" height="3" fill="${col}">
      <title>${esc(d.date)}  ${esc(d.verdict)}  level ${d.level_pct}%  ${esc(d.reason || '')}</title></rect>`;
  }
  for (const tk of timeTicks(x, 6)) {
    s += `<text x="${tk.px.toFixed(1)}" y="${h - 6}" text-anchor="middle" class="tick">${esc(tk.label)}</text>`;
  }
  s += '</svg>';
  return s;
}

function empty(w, h, text) {
  return `<svg viewBox="0 0 ${w} ${h}" class="chart" preserveAspectRatio="none">
    <text x="${w / 2}" y="${h / 2}" text-anchor="middle" class="empty">${esc(text)}</text></svg>`;
}

export function legend(items) {
  return items.map((i) => `<span class="key"><i style="background:${i.color}"></i>${esc(i.name)}</span>`).join('');
}
