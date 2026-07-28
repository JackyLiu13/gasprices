// editor.js — drag, nudge and snap the panel layout, writing layout.json back.
//
// The boxes you drag are positioned from the *ink* each element actually laid
// down in the current state, not from declared rectangles, because most elements
// have no declared width: how wide "PETROCAN MAJMAC 4/15  CHEAPEST" is depends on
// the string. render.js hands that back as a trace (see Framebuffer.el).
//
// A 320x172 panel is pixel-sensitive, so dragging alone is not enough: the arrow
// keys are the precise instrument and the mouse is for the rough pass.

const SNAP = 3;          // px, in panel space
const NUDGE = 1;
const NUDGE_BIG = 10;

// Elements with a `size` are text; the rest are drawn shapes. Used to decide
// which overlaps are worth warning about — see checkWarnings.
const isText = (el) => 'size' in el;

// Which layout.json fields a drag is allowed to change. Anything absent from an
// element is a field it has no use for, and inventing one would put geometry in
// layout.json that ui.h never reads.
const movesX = (el) => el.anchor !== 'center';
const resizes = (el) => ['w', 'h'].filter((f) => f in el);

export class LayoutEditor {
  /**
   * @param overlay  a positioned div exactly covering the scaled canvas
   * @param scale    canvas CSS pixels per panel pixel
   * @param layout   the live layout object; mutated in place
   * @param onChange called after every geometry change, to re-render
   * @param onSelect called with the selected element name, or null
   */
  constructor({ overlay, scale, layout, onChange, onSelect }) {
    this.overlay = overlay;
    this.scale = scale;
    this.layout = layout;
    this.onChange = onChange || (() => {});
    this.onSelect = onSelect || (() => {});
    this.boxes = new Map();
    this.selected = null;
    this.trace = {};
    this.guides = [];
    this.dirty = false;

    this.overlay.addEventListener('pointerdown', (e) => {
      if (e.target === this.overlay) this.select(null);
    });
  }

  // Reposition every box from a fresh render trace. Called after each render so
  // the handles follow the pixels rather than drifting away from them.
  update(trace) {
    this.trace = trace || {};
    for (const name of Object.keys(this.layout.elements)) {
      let box = this.boxes.get(name);
      if (!box) {
        box = this._makeBox(name);
        this.boxes.set(name, box);
      }
      this._place(name, box);
    }
    if (this.selected) this._syncReadout();
  }

  bounds(name) {
    const t = this.trace[name];
    const el = this.layout.elements[name];
    if (t && t.ink > 0) {
      return { x: t.x0, y: t.y0, w: t.x1 - t.x0 + 1, h: t.y1 - t.y0 + 1,
               drawn: true, clipped: t.clipped };
    }
    // Not drawn in this state (no level bar when the level is unknown, no
    // savings line on a bare feed). Show a marker where it would start, so it can
    // still be positioned, and say so rather than hiding it.
    return { x: el.x || 0, y: el.y || 0, w: Math.max(6, (el.w || 0)),
             h: Math.max(8, (el.h || 0)), drawn: false, clipped: false };
  }

  _makeBox(name) {
    const el = this.layout.elements[name];
    const box = document.createElement('div');
    box.className = 'elbox';
    box.tabIndex = 0;
    box.dataset.name = name;

    const tag = document.createElement('span');
    tag.className = 'eltag';
    tag.textContent = name;
    box.appendChild(tag);

    for (const f of resizes(el)) {
      const grip = document.createElement('div');
      grip.className = `grip grip-${f}`;
      grip.dataset.field = f;
      box.appendChild(grip);
    }

    box.addEventListener('pointerdown', (e) => this._onDown(e, name));
    box.addEventListener('keydown', (e) => this._onKey(e, name));
    box.addEventListener('focus', () => this.select(name));
    this.overlay.appendChild(box);
    return box;
  }

  _place(name, box) {
    const b = this.bounds(name);
    const s = this.scale;
    box.style.left = `${b.x * s}px`;
    box.style.top = `${b.y * s}px`;
    box.style.width = `${b.w * s}px`;
    box.style.height = `${b.h * s}px`;
    box.classList.toggle('undrawn', !b.drawn);
    box.classList.toggle('clipped', b.clipped);
    box.classList.toggle('selected', this.selected === name);
  }

  select(name) {
    this.selected = name;
    for (const [n, box] of this.boxes) box.classList.toggle('selected', n === name);
    this.onSelect(name);
  }

  // --- dragging ---

  _onDown(e, name) {
    e.preventDefault();
    e.stopPropagation();
    this.select(name);
    this.boxes.get(name).focus({ preventScroll: true });

    const el = this.layout.elements[name];
    const field = e.target.dataset.field || null;   // set on a resize grip
    const start = { px: e.clientX, py: e.clientY, ...el };
    const box0 = this.bounds(name);
    const others = this._snapLines(name);

    const move = (ev) => {
      let dx = Math.round((ev.clientX - start.px) / this.scale);
      let dy = Math.round((ev.clientY - start.py) / this.scale);

      if (field) {
        // Resizing: only the dragged dimension moves, and never below 1px.
        if (field === 'w') el.w = Math.max(1, start.w + dx);
        if (field === 'h') el.h = Math.max(1, start.h + dy);
      } else {
        const snapped = this._snap(box0, dx, dy, others);
        dx = snapped.dx;
        dy = snapped.dy;
        this._showGuides(snapped.lines);
        if (movesX(el)) el.x = start.x + dx;
        if ('y' in el) el.y = start.y + dy;
      }
      this.dirty = true;
      this.onChange();
    };

    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      this._showGuides([]);
      this.onChange();
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  _onKey(e, name) {
    const el = this.layout.elements[name];
    const step = e.shiftKey ? NUDGE_BIG : NUDGE;
    let dx = 0, dy = 0;
    switch (e.key) {
      case 'ArrowLeft': dx = -step; break;
      case 'ArrowRight': dx = step; break;
      case 'ArrowUp': dy = -step; break;
      case 'ArrowDown': dy = step; break;
      case 'Escape': this.boxes.get(name).blur(); this.select(null); return;
      default: return;
    }
    e.preventDefault();
    // Alt turns the arrows into a resize, for the four elements that have a
    // declared size — same keys, so you never have to reach for the mouse.
    const fields = resizes(el);
    if (e.altKey && fields.length) {
      if (dx && fields.includes('w')) el.w = Math.max(1, el.w + dx);
      if (dy && fields.includes('h')) el.h = Math.max(1, el.h + dy);
    } else {
      if (dx && movesX(el)) el.x += dx;
      if (dy && 'y' in el) el.y += dy;
    }
    this.dirty = true;
    this.onChange();
  }

  // --- snapping ---

  // Panel edges, plus every other element's drawn edges. Snapping to what is
  // actually on screen is the point: aligning to a neighbour's declared x when
  // the neighbour is right-aligned would align to nothing you can see.
  _snapLines(exclude) {
    const xs = [0, this.layout.panel.w - 1];
    const ys = [0, this.layout.panel.h - 1];
    for (const name of Object.keys(this.layout.elements)) {
      if (name === exclude) continue;
      const b = this.bounds(name);
      if (!b.drawn) continue;
      xs.push(b.x, b.x + b.w - 1);
      ys.push(b.y, b.y + b.h - 1);
    }
    return { xs, ys };
  }

  _snap(box0, dx, dy, { xs, ys }) {
    const lines = [];
    const fit = (edges, candidates, delta) => {
      let best = null;
      for (const edge of edges) {
        for (const c of candidates) {
          const off = c - (edge + delta);
          if (Math.abs(off) <= SNAP && (best === null || Math.abs(off) < Math.abs(best.off))) {
            best = { off, at: c };
          }
        }
      }
      return best;
    };

    // Only snap an axis the pointer is actually moving on. Otherwise a purely
    // horizontal drag that happens to start near a horizontal guide would
    // silently shift the element vertically as well.
    if (dx !== 0) {
      const bx = fit([box0.x, box0.x + box0.w - 1], xs, dx);
      if (bx) { dx += bx.off; lines.push({ axis: 'x', at: bx.at }); }
    }
    if (dy !== 0) {
      const by = fit([box0.y, box0.y + box0.h - 1], ys, dy);
      if (by) { dy += by.off; lines.push({ axis: 'y', at: by.at }); }
    }
    return { dx, dy, lines };
  }

  _showGuides(lines) {
    for (const g of this.guides) g.remove();
    this.guides = lines.map(({ axis, at }) => {
      const g = document.createElement('div');
      g.className = `guide guide-${axis}`;
      if (axis === 'x') g.style.left = `${at * this.scale}px`;
      else g.style.top = `${at * this.scale}px`;
      this.overlay.appendChild(g);
      return g;
    });
  }

  _syncReadout() {
    const el = this.layout.elements[this.selected];
    const parts = ['x', 'y', 'w', 'h', 'size', 'dot_r']
      .filter((f) => f in el)
      .map((f) => `${f} ${el[f]}`);
    this.onSelect(this.selected, parts.join('   '));
  }
}

// Overflow and collision checks, run after every render.
//
// "Any two boxes overlap" would cry wolf constantly: the verdict text is
// *supposed* to sit inside the verdict bar, and the level text above the level
// bar. Text over a filled shape is the design. Text over text never is — so that
// is what gets flagged, along with anything leaving the panel, which is the bug
// this harness was built to catch in the first place.
export function checkWarnings(layout, trace) {
  const warnings = [];
  const drawn = [];

  for (const [name, el] of Object.entries(layout.elements)) {
    const t = trace[name];
    if (!t || t.ink === 0) continue;
    const b = { name, x: t.x0, y: t.y0, x1: t.x1, y1: t.y1, text: isText(el) };
    drawn.push(b);
    if (t.clipped) {
      warnings.push(`${name} is drawn outside the ` +
                    `${layout.panel.w}x${layout.panel.h} panel`);
    }
  }

  for (let i = 0; i < drawn.length; i++) {
    for (let j = i + 1; j < drawn.length; j++) {
      const a = drawn[i], b = drawn[j];
      if (!a.text || !b.text) continue;
      if (a.x > b.x1 || b.x > a.x1 || a.y > b.y1 || b.y > a.y1) continue;
      warnings.push(`${a.name} and ${b.name} overlap`);
    }
  }
  return warnings;
}
