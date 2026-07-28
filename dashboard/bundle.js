/* "Copy for AI" — a self-contained context bundle.
 *
 * The goal is that pasting this into a fresh agent session is enough to reason
 * about the model without a single follow-up file read. Two things make or
 * break that, and both are stated up front in the preamble rather than left for
 * the reader to infer:
 *
 *   1. Prices are integers in tenths of a cent in the JSON, and floats in $/L
 *      in the CSVs. An agent that mixes them is out by 1000x.
 *   2. docs/data.json is shifted to the cheapest station; the CSVs are not.
 *      An agent that compares them directly finds a discrepancy that isn't real
 *      and "fixes" it.
 *
 * Raw CSV rather than prose tables: an agent parses it more reliably and it
 * costs fewer tokens. Date-sliced so the bundle stays bounded as history grows.
 */

const MAX_ROWS = 400;   // keeps the bundle a sane size once history is years long

function sliceCsv(text, days) {
  if (!text) return '(empty)';
  const lines = text.trim().split('\n');
  const header = lines[0];
  let rows = lines.slice(1);
  if (days) {
    const cutoff = new Date(Date.now() - days * 86400000).toISOString().slice(0, 10);
    rows = rows.filter((l) => l.slice(0, 10) >= cutoff);
  }
  let note = '';
  if (rows.length > MAX_ROWS) {
    note = `\n... ${rows.length - MAX_ROWS} older rows omitted ...\n`;
    rows = rows.slice(-MAX_ROWS);
  }
  return `${header}\n${note}${rows.join('\n')}`;
}

function accuracyTable(a, variant) {
  const rows = (a?.buckets || []).filter((b) => !variant || b.variant === variant);
  if (!rows.length) return 'No forecasts have been scored yet.';
  return ['variant,horizon,n,mae_cents,bias_cents,no_move_mae_cents,edge_cents']
    .concat(rows.map((b) => [b.variant, b.horizon, b.n,
      b.mae_cents.toFixed(3), b.bias_cents.toFixed(3),
      b.baseline_mae_cents.toFixed(3), b.edge_cents.toFixed(3)].join(',')))
    .join('\n');
}

function stationTable(s) {
  if (!s) return '(unavailable)';
  return ['id,label,city,role,predicted_cad_l,offset_cad_l,mad_cad_l,n,confident,last_seen']
    .concat(s.stations.map((x) => [x.id, x.label, x.city, x.role,
      x.predicted == null ? '' : x.predicted.toFixed(4),
      x.offset == null ? '' : x.offset.toFixed(4),
      x.mad == null ? '' : x.mad.toFixed(4),
      x.n, x.confident, x.last_seen].join(',')))
    .join('\n');
}

export function bundleAll(state, days) {
  const o = state.overview || {};
  const p = o.published || {};
  const m = p.meta || {};
  const raw = state.raw || {};
  const win = days ? `${days} days` : 'all';

  return `# gasprices — model context bundle
Generated ${new Date().toISOString()} from the local dashboard.
Window: ${win}.

## Read these first
- \`AGENT_NOTES.md\` — how to interpret requests on this project, what counts as
  an improvement, and what must not change silently. **Read it before proposing
  a model change.**
- \`CLAUDE.md\` — code facts, conventions, and the findings that shaped the design.
- \`DASHBOARD.md\` — how this dashboard and the forecast log are put together.

Relevant source: \`backend/model.py\` (tax stack, passthrough, calibration),
\`backend/verdict.py\` + \`firmware/gasprices/verdict.h\` + \`preview/verdict.js\`
(three copies of one engine, pinned by \`tests/vectors.csv\`),
\`backend/analytics.py\` (the derivations below), \`backend/build.py\` (orchestration).

## UNITS — get these wrong and every number below is nonsense
- **docs/data.json uses integers in tenths of a cent per litre.** \`1709\` = $1.709/L.
- **The CSVs use floats in dollars per litre.** \`1.709\`.
- **Errors and margins here are quoted in cents per litre (c/L).**
- **docs/data.json is station-shifted.** \`today_cad\`, \`pred\`, \`window_lo/hi\` and
  \`hist\` are all rebased to the cheapest tracked station by
  \`meta.station_shift_cad_l\` (currently ${m.station_shift_cad_l ?? 'n/a'}).
  The CSVs and every table below are in **regional benchmark space**. They are
  supposed to differ by exactly that shift. Do not "fix" it.
- **Windows are date-based, never row-based.** history.csv is weekly for its first
  ~60 rows and daily after. Slicing "the last N rows" silently means months in
  one regime and weeks in the other. This has caused two real bugs.

## Current state
- Published verdict: **${p.verdict_hint || '?'}** ("${p.reason_hint || ''}")
- Benchmark: ${m.benchmark_cad_l ?? '?'} $/L; target ${m.target_cad_l ?? '?'} $/L
- Calibrated margin: ${m.margin_cad_l ?? '?'} $/L over a ${o.model?.margin_window_days ?? 90}-day window
- Wholesale ${m.wholesale_cad_l ?? '?'} $/L, RBOB ${m.rbob_usd_gal ?? '?'} USD/gal, USDCAD ${m.usd_cad ?? '?'}
- Level source: ${m.level_source || '?'}
- History: ${o.history_rows ?? '?'} rows, of which ${o.logged_prices ?? '?'} are logged pump
  prices and ${o.survey_rows ?? '?'} are Ontario survey rows.

## Engine config (verdict.Config — must match verdict.h gp_default_config())
${JSON.stringify(o.config || {}, null, 1)}

## Model constants (backend/model.py)
${JSON.stringify(o.model || {}, null, 1)}

## Forecast accuracy — model vs assuming no move
Scored from backend/forecasts.csv against the benchmark on each target date.
A negative \`edge_cents\` means the model LOST to doing nothing.
Sample sizes are small; check \`n\` before drawing any conclusion.

\`\`\`csv
${accuracyTable(state.accuracy)}
\`\`\`

## Stations — offsets, not prices
offset = median(observed - benchmark) over 180 days; price = benchmark + offset.
\`mad\` is the median absolute deviation of those deltas, i.e. how much the
observations disagreed. \`confident\` is n >= 3.

\`\`\`csv
${stationTable(state.stations)}
\`\`\`

## docs/data.json (published, station-shifted)
\`\`\`json
${JSON.stringify(p, null, 1)}
\`\`\`

## backend/history.csv (regional benchmark, ${win})
\`\`\`csv
${sliceCsv(raw.history_csv, days)}
\`\`\`

## backend/forecasts.csv (every forecast committed to, benchmark space)
\`\`\`csv
${sliceCsv(raw.forecasts_csv, days)}
\`\`\`

## backend/station_prices.csv (observations)
\`\`\`csv
${sliceCsv(raw.station_prices_csv, 0)}
\`\`\`

## backend/stations.csv (registry)
\`\`\`csv
${sliceCsv(raw.stations_csv, 0)}
\`\`\`
`;
}

export async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // Clipboard API needs a secure context; 127.0.0.1 usually qualifies, but
    // fall back rather than lose the bundle.
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
  }
}
