// The Interactions tab's "brain" layout (issue #194): members are neurons,
// and each data category they exchange is its own thread between them.
// Pure and deterministic: no physics, no randomness.
import { CATEGORIES } from "./visual.js";

const ROLE_ORDER = { hub: 0, member: 1, observer: 2, free: 3 };
export const THREAD_GAP = 9;

/** 1 for 1-9, 2 for 10-49, 3 for 50-199, 4 for 200-999, 5 for 1000+ (0 for none). */
export function threadCount(count) {
  if (!(count > 0)) return 0;
  if (count < 10) return 1;
  if (count < 50) return 2;
  if (count < 200) return 3;
  if (count < 1000) return 4;
  return 5;
}

/**
 * Positions keyed by name: the subject member (or, for a swarm, its hub or
 * else its first member by name) in the centre; everyone else on a ring,
 * sorted by role, then name.
 */
export function layout(nodes, subject, { width = 640, height = 480 } = {}) {
  const cx = width / 2;
  const cy = height / 2;
  const sorted = [...nodes].sort((a, b) =>
    (ROLE_ORDER[a.role] ?? 9) - (ROLE_ORDER[b.role] ?? 9) || a.name.localeCompare(b.name));
  let centre = null;
  if (subject?.type === "member") centre = nodes.find((n) => n.name === subject.name)?.name ?? null;
  else {
    const inSwarm = sorted.filter((n) => n.swarm === subject?.name);
    centre = (inSwarm.find((n) => n.is_hub) ?? inSwarm[0] ?? sorted[0])?.name ?? null;
  }
  const ring = sorted.filter((n) => n.name !== centre);
  const r = Math.min(width, height) * 0.38;
  const positions = {};
  if (centre) positions[centre] = { x: cx, y: cy, centre: true };
  ring.forEach((n, i) => {
    const angle = -Math.PI / 2 + (2 * Math.PI * i) / Math.max(1, ring.length);
    positions[n.name] = { x: cx + r * Math.cos(angle), y: cy + r * Math.sin(angle), centre: false };
  });
  return { centre, positions };
}

// A small deterministic bow per thread, from its name: the same input always draws the same curve.
function bow(seed) {
  let h = 0;
  for (const ch of seed) h = (h * 31 + ch.charCodeAt(0)) | 0;
  return ((Math.abs(h) % 7) - 3) * 1.5;
}

/**
 * Threads for every edge: per pair, every category's threads side by side as
 * quadratic Béziers between the two neurons, each with its own perpendicular
 * offset (THREAD_GAP apart, centred) plus a small bow, so the bundle fans out
 * in the middle. Returns [{ d, category, primary, a, b, offset }].
 */
export function strands(edges, positions) {
  const pairs = new Map();
  for (const e of edges) {
    if (!positions[e.a] || !positions[e.b]) continue;
    const key = `${e.a}|${e.b}`;
    if (!pairs.has(key)) pairs.set(key, []);
    pairs.get(key).push(e);
  }
  const order = Object.fromEntries(CATEGORIES.map((c, i) => [c.key, i]));
  const out = [];
  for (const [key, list] of pairs) {
    const [a, b] = key.split("|");
    const pa = positions[a];
    const pb = positions[b];
    const dx = pb.x - pa.x;
    const dy = pb.y - pa.y;
    const len = Math.hypot(dx, dy) || 1;
    const nx = -dy / len;
    const ny = dx / len;
    const threads = [];
    for (const e of [...list].sort((x, y) => (order[x.category] ?? 9) - (order[y.category] ?? 9))) {
      for (let i = 0; i < threadCount(e.count); i += 1) threads.push({ category: e.category, primary: i === 0 });
    }
    threads.forEach((t, i) => {
      const offset = (i - (threads.length - 1) / 2) * THREAD_GAP;
      const k = offset + bow(`${key}:${i}`);
      const mx = (pa.x + pb.x) / 2 + nx * k * 2.2;
      const my = (pa.y + pb.y) / 2 + ny * k * 2.2;
      out.push({ ...t, a, b, offset,
        d: `M${pa.x.toFixed(1)} ${pa.y.toFixed(1)}Q${mx.toFixed(1)} ${my.toFixed(1)} ${pb.x.toFixed(1)} ${pb.y.toFixed(1)}` });
    });
  }
  return out;
}

/** Seven short curved dendrites around a neuron at (x, y) with radius r. */
export function dendrites(x, y, r) {
  return Array.from({ length: 7 }, (_, i) => {
    const a = (2 * Math.PI * i) / 7 + 0.3;
    const x1 = x + Math.cos(a) * r;
    const y1 = y + Math.sin(a) * r;
    const x2 = x + Math.cos(a) * r * 1.9;
    const y2 = y + Math.sin(a) * r * 1.9;
    const cxp = x + Math.cos(a + 0.35) * r * 1.5;
    const cyp = y + Math.sin(a + 0.35) * r * 1.5;
    return `M${x1.toFixed(1)} ${y1.toFixed(1)}Q${cxp.toFixed(1)} ${cyp.toFixed(1)} ${x2.toFixed(1)} ${y2.toFixed(1)}`;
  });
}
