/* Panel wiring for the dashboard.
 *
 * Two modes, one page. Served by dashboard/server.py you get the full API and
 * the write controls; opened as a static file (e.g. from docs/ on Pages) the
 * /api/ calls 404, the write section stays hidden, and the read-only panels
 * render from docs/data.json. Feature detection rather than a second build.
 */

import * as ch from './charts.js';
import { bundleAll, copy } from './bundle.js';
import { evaluate, reason, TANK, DEFAULT_CONFIG } from '../preview/verdict.js';

const $ = (id) => document.getElementById(id);
const state = { live: false, overview: null, series: null, stations: null,
                accuracy: null, replay: null, raw: null };

function say(msg, kind = '') {
  const el = $('status');
  el.textContent = msg;
  el.className = kind;
}

async function api(path) {
  const r = await fetch(path, { headers: { Accept: 'application/json' } });
  if (!r.ok) throw new Error(`${path} -> ${r.status}`);
  return r.json();
}

function localToday() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

const price = (v) => (v == null ? '—' : `$${v.toFixed(3)}`);
const cents = (v) => (v == null ? '—' : `${(v * 100).toFixed(1)}c`);
const signed = (v) => (v == null ? '—' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}c`);

/* --- overview ------------------------------------------------------------- */

function renderOverview() {
  const o = state.overview;
  const p = o.published || {};
  const m = p.meta || {};
  const stats = [
    ['today', p.today_cad ? `$${(p.today_cad / 1000).toFixed(3)}` : '—', 'at cheapest station'],
    ['verdict', p.verdict_hint || '—', p.reason_hint || ''],
    ['benchmark', price(m.benchmark_cad_l), 'regional'],
    ['margin', cents(m.margin_cad_l), `${o.model.margin_window_days}d median`],
    ['target', price(m.target_cad_l), 'equilibrium'],
    ['history', `${o.history_rows}`, `${o.logged_prices} logged, ${o.survey_rows} survey`],
  ];
  $('stats').innerHTML = stats.map(([k, v, sub]) => {
    const cls = k === 'verdict' ? ({ GREAT: 'g', FILL_NOW: 'g', NEUTRAL: 'a',
      WAIT: 'r', EXPENSIVE: 'r' }[v] || 'd') : '';
    return `<div class="stat"><b class="${cls}">${v}</b><span>${k}</span>
      <span class="d" style="text-transform:none;letter-spacing:0">${sub}</span></div>`;
  }).join('');

  if (m.station_shift_cad_l) {
    $('published-note').innerHTML = `<div class="note">The published feed is shifted
      <b>${signed(m.station_shift_cad_l)}</b> to price everything at the cheapest
      station. Every chart below undoes that and works in regional benchmark space,
      so the two will not match by design. Level source: <b>${m.level_source || '?'}</b>.</div>`;
  }
}

/* --- price / target ------------------------------------------------------- */

function renderPrice() {
  const s = state.series;
  $('chart-price').innerHTML = ch.lineChart([
    { name: 'benchmark', points: s.benchmark, color: ch.C.white, dots: true, width: 1.6 },
    { name: 'target', points: s.target, color: ch.C.amber, dash: '4 3' },
  ], { format: (v) => v.toFixed(2) });
  $('key-price').innerHTML = ch.legend([
    { name: 'benchmark (what we believe the pump was)', color: ch.C.white },
    { name: 'equilibrium target', color: ch.C.amber },
    { name: 'logged price', color: ch.C.cyan },
  ]);
}

/* --- margin --------------------------------------------------------------- */

function renderMargin() {
  const s = state.series;
  $('chart-margin').innerHTML = ch.scatterChart(s.margin, s.margin_rolling, {
    format: (v) => `${(v * 100).toFixed(0)}c`,
  });
  const r = s.margin_rolling;
  if (r.length >= 2) {
    const drift = (r[r.length - 1].v - r[0].v) * 100;
    $('margin-note').innerHTML = `<div class="note thin">Rolling margin moved
      <b>${drift >= 0 ? '+' : ''}${drift.toFixed(1)}c/L</b> across this window
      (${r[0].date} → ${r[r.length - 1].date}). It is not a constant; a fixed
      value goes stale, which is why the window is ${s.margin_window_days} days.</div>`;
  } else {
    $('margin-note').innerHTML = `<div class="note">Not enough observations inside
      the ${s.margin_window_days}-day window for a rolling median yet — the model
      is running on its cold-start default.</div>`;
  }
}

/* --- accuracy ------------------------------------------------------------- */

function renderAccuracy() {
  const a = state.accuracy;
  const sel = $('variant');
  if (!sel.options.length) {
    sel.innerHTML = a.variants.map((v) =>
      `<option ${v === a.default_variant ? 'selected' : ''}>${v}</option>`).join('');
    sel.onchange = renderAccuracy;
  }
  const want = sel.value || a.default_variant;
  const rows = a.buckets.filter((b) => b.variant === want).sort((x, y) => x.horizon - y.horizon);

  $('chart-accuracy').innerHTML = ch.barChart(rows.map((b) => ({
    label: `+${b.horizon}d`,
    bars: [
      { name: 'model MAE', v: b.mae_cents, color: ch.C.cyan },
      { name: 'no-move MAE', v: b.baseline_mae_cents, color: ch.C.grey },
    ],
  })), { format: (v) => `${v.toFixed(1)}c`, emptyText: 'no scored forecasts for this variant yet' });

  $('key-accuracy').innerHTML = ch.legend([
    { name: 'model MAE', color: ch.C.cyan },
    { name: 'assume no move', color: ch.C.grey },
  ]);

  $('accuracy-table').innerHTML = rows.length ? `
    <tr><th>horizon</th><th class="num">n</th><th class="num">MAE</th>
      <th class="num">bias</th><th class="num">no-move</th><th class="num">edge</th>
      <th>window</th></tr>
    ${rows.map((b) => `<tr>
      <td>+${b.horizon}d</td><td class="num">${b.n}</td>
      <td class="num">${b.mae_cents.toFixed(2)}c</td>
      <td class="num ${b.bias_cents > 0 ? 'a' : 'd'}">${b.bias_cents >= 0 ? '+' : ''}${b.bias_cents.toFixed(2)}c</td>
      <td class="num d">${b.baseline_mae_cents.toFixed(2)}c</td>
      <td class="num ${b.edge_cents >= 0 ? 'g' : 'r'}">${b.edge_cents >= 0 ? '+' : ''}${b.edge_cents.toFixed(2)}c</td>
      <td class="d">${b.first} → ${b.last}</td></tr>`).join('')}` : '';

  const n = rows.reduce((t, b) => t + b.n, 0);
  // The sample is tiny at first and saying so is the whole point — a confident
  // line through three points is worse than an empty panel.
  $('accuracy-note').innerHTML = !a.forecast_count
    ? `<div class="note">No forecasts logged yet. build.py appends to
       backend/forecasts.csv on every run, so this fills in from the next one.</div>`
    : n < 20
      ? `<div class="note">Only <b>${n}</b> scored forecast(s) for this variant.
         Read nothing into the sign of the edge yet — at this sample a single
         surprising day flips it. Roughly 30+ per horizon before it means anything.</div>`
      : '';
}

/* --- verdict replay ------------------------------------------------------- */

function renderVerdicts() {
  const tank = $('tank').value;
  const days = (state.replay?.days || []).map((d) => {
    const input = { today: d.today, pred: d.pred, window_lo: d.window_lo,
      window_hi: d.window_hi, age_minutes: 0, tank: TANK[tank] };
    const r = evaluate(input);
    return { date: d.date, verdict: r.verdict, level_pct: r.level_pct,
             reason: reason(input, r) };
  });
  $('chart-verdicts').innerHTML = ch.verdictStrip(days);

  const counts = {};
  for (const d of days) counts[d.verdict] = (counts[d.verdict] || 0) + 1;
  $('key-verdicts').innerHTML = ch.legend(Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .map(([v, n]) => ({ name: `${v} ${(100 * n / days.length).toFixed(0)}%`,
                        color: ch.verdictColor(v) })));
}

/* --- stations ------------------------------------------------------------- */

function renderStations() {
  const s = state.stations;
  const rows = s.stations.map((st) => {
    const unpriced = st.predicted == null;
    return `<tr class="${st.is_best ? 'best' : ''}">
      <td>${st.is_best ? '<span class="tag g">CHEAPEST</span> ' : ''}${st.label}</td>
      <td class="d">${st.brand}</td>
      <td class="d">${st.city}</td>
      <td>${st.role === 'regular' ? '<span class="tag c">usual</span>' : `<span class="d">${st.role}</span>`}</td>
      <td class="num">${unpriced ? '<span class="r">no data</span>' : price(st.predicted)}</td>
      <td class="num ${st.offset < 0 ? 'g' : st.offset > 0 ? 'r' : 'd'}">${st.offset == null ? '—' : signed(st.offset)}</td>
      <td class="num d">${st.mad == null ? '—' : `±${(st.mad * 100).toFixed(1)}c`}</td>
      <td class="num ${st.confident ? 'g' : 'a'}">${st.n}${st.confident ? '' : '?'}</td>
      <td class="d">${st.last_seen || '—'}</td></tr>`;
  }).join('');

  $('stations-table').innerHTML = `
    <tr><th>station</th><th>brand</th><th>city</th><th>role</th>
      <th class="num">price</th><th class="num">offset</th><th class="num">spread</th>
      <th class="num">n</th><th>last seen</th></tr>${rows}`;

  const unpriced = s.stations.filter((x) => x.predicted == null);
  const thin = s.stations.filter((x) => x.predicted != null && !x.confident);
  const bits = [];
  if (unpriced.length) {
    bits.push(`<b>${unpriced.length} station(s) have no offset at all</b>
      (${unpriced.map((x) => x.label).join(', ')}). One logged price each is enough
      to bring them in.`);
  }
  if (thin.length) {
    bits.push(`${thin.length} rest on fewer than ${s.confident_at} observations
      (the <b>?</b>), so their price is an extrapolation from a single sighting.`);
  }
  $('stations-note').innerHTML = bits.length
    ? `<div class="note">${bits.join(' ')} Offsets are structural and hold for weeks —
       this is the cheapest accuracy you can buy.</div>` : '';
}

/* --- sweep ---------------------------------------------------------------- */

async function runSweep() {
  const btn = $('run-sweep');
  btn.disabled = true;
  say('running the sweep…', 'busy');
  try {
    const s = await api('/api/sweep');
    if (!s.ready) {
      $('sweep-note').innerHTML = `<div class="note">Need ~${s.need} days, have ${s.have}.</div>`;
      say('not enough history for a sweep', '');
      return;
    }
    $('chart-sweep').innerHTML = ch.heatmap(s.grid,
      s.thresholds.map((t) => `${(t / 10).toFixed(1)}c`),
      s.horizons.map((h) => `${h}d`));
    const c = s.current;
    const capture = c.oracle_edge_cents > 0
      ? (100 * c.edge_cents / c.oracle_edge_cents).toFixed(0) : '—';
    $('sweep-note').innerHTML = `<div class="note thin">
      ${s.days} days (${s.observed} observed, the rest held flat between).
      Live config — threshold ${(c.threshold / 10).toFixed(1)}c, horizon ${c.horizon} —
      pays <b class="${c.edge_cents >= 0 ? 'g' : 'r'}">${c.edge_cents >= 0 ? '+' : ''}${c.edge_cents.toFixed(2)}c/L</b>
      against the baseline, on ${c.fills} fills vs ${c.baseline_fills}.
      Perfect foresight would pay <b>${c.oracle_edge_cents.toFixed(2)}c/L</b>, so the
      rules already capture about <b>${capture}%</b> of what is there to get —
      better forecasting has that much headroom, and no more.</div>`;
    say('sweep done', 'ok');
  } catch (e) {
    say(`sweep failed: ${e.message}`, 'bad');
  } finally {
    btn.disabled = false;
  }
}

/* --- coverage ------------------------------------------------------------- */

function renderCoverage() {
  const s = state.series;
  $('chart-coverage').innerHTML = ch.coverageStrip(s.coverage, s.fields);
}

/* --- writes --------------------------------------------------------------- */

function setupWrites() {
  if (!state.live) return;
  $('writes').hidden = false;
  // Local date, not toISOString(): after ~8pm Toronto that returns tomorrow in
  // UTC, so the form would default to a future date and log_price.py would
  // (rightly) refuse it.
  $('w-date').value = localToday();
  $('w-station').innerHTML = '<option value="">regional benchmark</option>'
    + state.stations.stations.map((s) =>
      `<option value="${s.id}">${s.label} — ${s.city}</option>`).join('');

  const out = $('w-out');
  const show = (text, ok) => {
    out.hidden = false;
    out.textContent = text || '(no output)';
    say(ok ? 'done' : 'that did not work', ok ? 'ok' : 'bad');
  };

  $('w-log').onclick = async () => {
    const price = parseFloat($('w-price').value);
    if (!Number.isFinite(price)) { say('enter a price first', 'bad'); return; }
    say('logging…', 'busy');
    const r = await fetch('/api/price', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ price, station: $('w-station').value || null,
                             date: $('w-date').value || null }),
    });
    const j = await r.json();
    show(j.output || j.error, j.ok);
    if (j.ok) { $('w-price').value = ''; await loadAll(); }
  };

  $('w-refresh').onclick = async () => {
    say('running build.py — fetching RBOB, FX and the Ontario survey…', 'busy');
    $('w-refresh').disabled = true;
    try {
      const r = await fetch('/api/refresh', { method: 'POST' });
      const j = await r.json();
      show(j.output || j.error, j.ok);
      if (j.ok) await loadAll();
    } catch (e) {
      show(String(e), false);
    } finally {
      $('w-refresh').disabled = false;
    }
  };
}

/* --- load ----------------------------------------------------------------- */

async function loadReadOnly() {
  // Static fallback: no server, so docs/data.json is all there is.
  const published = await (await fetch('../docs/data.json')).json();
  state.overview = {
    published, history_rows: (published.hist || []).length,
    logged_prices: 0, survey_rows: 0,
    config: {}, model: { margin_window_days: 90 },
  };
  renderOverview();
  document.querySelectorAll('section.card').forEach((s) => {
    if (s.id !== 'overview' && !s.querySelector('#stats')) {
      const holder = s.querySelector('div[id^="chart-"], table');
      if (holder && !holder.innerHTML) {
        holder.innerHTML = '<div class="note">Needs the local server: '
          + '<b>python3 dashboard/server.py</b></div>';
      }
    }
  });
  say('read-only: showing the published feed. Run dashboard/server.py for the full picture.', '');
}

async function loadAll() {
  const days = parseInt($('range').value, 10);
  const q = days ? `?days=${days}` : '';
  const [overview, series, stationsData, accuracy, replay] = await Promise.all([
    api('/api/overview'), api(`/api/series${q}`), api('/api/stations'),
    api('/api/accuracy'), api('/api/replay'),
  ]);
  Object.assign(state, { overview, series, stations: stationsData, accuracy, replay });
  renderOverview();
  renderPrice();
  renderMargin();
  renderAccuracy();
  renderVerdicts();
  renderStations();
  renderCoverage();
  say(`${overview.history_rows} history rows, ${accuracy.forecast_count} logged forecasts,`
    + ` ${stationsData.stations.length} stations — as of ${overview.generated}`, 'ok');
}

async function boot() {
  try {
    await api('/api/overview');
    state.live = true;
  } catch {
    state.live = false;
  }

  try {
    if (state.live) {
      await loadAll();
      setupWrites();
    } else {
      await loadReadOnly();
    }
  } catch (e) {
    say(`failed to load: ${e.message}`, 'bad');
    return;
  }

  $('range').onchange = () => { if (state.live) loadAll().catch((e) => say(e.message, 'bad')); };
  $('tank').onchange = renderVerdicts;
  $('reload').onclick = () => (state.live ? loadAll() : loadReadOnly());
  $('run-sweep').onclick = runSweep;
  $('run-sweep').disabled = !state.live;
  $('copy-all').onclick = async () => {
    try {
      say('building the bundle…', 'busy');
      if (state.live && !state.raw) state.raw = await api('/api/raw');
      const text = bundleAll(state, parseInt($('range').value, 10) || 0);
      await copy(text);
      say(`copied ${(text.length / 1024).toFixed(1)} KB — paste it into a fresh agent session`, 'ok');
    } catch (e) {
      say(`copy failed: ${e.message}`, 'bad');
    }
  };
}

boot();
