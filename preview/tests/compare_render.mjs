// compare_render.mjs — the gate for the whole browser preview.
//
// Renders every case in tests/vectors.csv with preview/render.js and compares it
// pixel for pixel against the PPM the C++ harness wrote from the real ui.h. If
// they ever differ, the preview is lying about what the panel shows, and a
// lying preview is worse than no preview at all — this is what stops the JS
// renderer quietly drifting from the firmware, the same way vectors.csv stops
// the two verdict engines drifting from each other.
//
//   make -C tests golden && node preview/tests/compare_render.mjs
//
// It also checks the JS verdict engine against vectors.csv's expect_* columns
// first, since a wrong verdict would show up as a puzzling pixel diff.

import { readFileSync, existsSync, readdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

import { parseVectors } from '../states.js';
import { render, renderMessage } from '../render.js';
import { verdictName, GP_REASON_MAX_CHARS, reason } from '../verdict.js';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const GOLDEN = join(ROOT, 'tests', 'out');

// Parse binary PPM (P6). Deliberately minimal: this only ever reads files
// tests/test_ui.cpp wrote, whose header is always "P6\n<w> <h>\n255\n".
function readPPM(path) {
  const buf = readFileSync(path);
  let pos = 0;
  const token = () => {
    while (pos < buf.length && /\s/.test(String.fromCharCode(buf[pos]))) pos++;
    const start = pos;
    while (pos < buf.length && !/\s/.test(String.fromCharCode(buf[pos]))) pos++;
    return buf.toString('ascii', start, pos);
  };
  const magic = token();
  if (magic !== 'P6') throw new Error(`${path}: not a P6 PPM (got ${magic})`);
  const w = parseInt(token(), 10);
  const h = parseInt(token(), 10);
  const maxval = parseInt(token(), 10);
  if (maxval !== 255) throw new Error(`${path}: maxval ${maxval}, expected 255`);
  pos++;                                     // single whitespace before the data
  const data = buf.subarray(pos);
  if (data.length !== w * h * 3)
    throw new Error(`${path}: ${data.length} bytes of pixel data, expected ${w * h * 3}`);
  return { w, h, data };
}

const hex = (r, g, b) =>
  '#' + [r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('');

// Compare and, on failure, say exactly where and what — a bare "images differ"
// sends you back to squinting at pixels, which is what this project is trying
// to stop doing.
function compare(name, fb, golden) {
  if (fb.w !== golden.w || fb.h !== golden.h) {
    return `${name}: size ${fb.w}x${fb.h}, golden ${golden.w}x${golden.h}`;
  }
  const mine = fb.toRGB();
  const theirs = golden.data;
  let diffs = 0;
  let first = null;
  for (let i = 0; i < mine.length; i += 3) {
    if (mine[i] === theirs[i] && mine[i + 1] === theirs[i + 1] &&
        mine[i + 2] === theirs[i + 2]) continue;
    diffs++;
    if (first === null) first = i / 3;
  }
  if (!diffs) return null;
  const x = first % fb.w, y = Math.trunc(first / fb.w);
  const o = first * 3;
  return `${name}: ${diffs} differing pixel(s); first at (${x},${y}) ` +
         `js ${hex(mine[o], mine[o + 1], mine[o + 2])} ` +
         `vs firmware ${hex(theirs[o], theirs[o + 1], theirs[o + 2])}`;
}

function main() {
  if (!existsSync(GOLDEN) || readdirSync(GOLDEN).length === 0) {
    console.error('no golden images. Run: make -C tests golden');
    return 2;
  }

  const layout = JSON.parse(
    readFileSync(join(ROOT, 'firmware', 'gasprices', 'layout.json'), 'utf8'));
  const states = parseVectors(
    readFileSync(join(ROOT, 'tests', 'vectors.csv'), 'utf8'));

  if (!states.length) {
    console.error('vectors.csv parsed to zero cases');
    return 2;
  }

  const failures = [];

  // --- the engine port, before any pixels ---
  for (const s of states) {
    const got = [verdictName(s.v.verdict).replace(' ', '_'), s.v.level_pct,
                 s.v.days_to_wait, s.v.save].join(' ');
    const want = [s.expect.verdict, s.expect.level,
                  s.expect.days, s.expect.save].join(' ');
    if (got !== want) {
      failures.push(`${s.name}: engine got [${got}] want [${want}]`);
    }
    const r = reason(s.input, s.v);
    if (r.length > GP_REASON_MAX_CHARS) {
      failures.push(`${s.name}: reason "${r}" is ${r.length} chars, ` +
                    `max ${GP_REASON_MAX_CHARS}`);
    }
  }

  // --- the boot screen, then every case ---
  const frames = [
    ['boot', renderMessage('gasprices', 'connecting...', layout)],
    ...states.map((s) => [s.name, render(s, layout)]),
  ];

  for (const [name, fb] of frames) {
    const path = join(GOLDEN, `${name}.ppm`);
    if (!existsSync(path)) {
      failures.push(`${name}: no golden image at tests/out/${name}.ppm`);
      continue;
    }
    const err = compare(name, fb, readPPM(path));
    if (err) failures.push(err);
    else console.log(`  ok   ${name}${fb.clipped ? '   (clipped)' : ''}`);
  }

  if (failures.length) {
    console.error('');
    for (const f of failures) console.error(`  FAIL ${f}`);
    console.error(`\n${failures.length} failure(s): ` +
                  'preview/render.js and firmware/gasprices/ui.h disagree');
    return 1;
  }

  console.log(`\n${frames.length} frames, 0 differing pixels — ` +
              'the preview matches the firmware');
  return 0;
}

process.exit(main());
