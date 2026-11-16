import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { CAT_SELF, CATEGORIES, LIGHT, LIGHT_COLORS, LTMS_CORE, MIND, NEUTRAL, RELAY, ROLES, VOID } from "./visual.js";
import { HOLO, contrast } from "./theme.js";
import {
  HEARTBEAT, PYRAMID_EDGES, breathe, clearGlowCache, createFlashGate, drawGlow, getCalmStart, glowCacheSize,
  glowSprite, heartbeatBpm, heartbeatSwell, prefersReducedMotion, pyramid, pyramidPoints, setCalmStart, smoothstep,
  withAlpha,
} from "./light.js";

afterEach(() => {
  clearGlowCache();
  vi.restoreAllMocks();
  try { window.localStorage.clear(); } catch { /* ignore */ }
});

describe("Holo Light tokens (#400)", () => {
  it("has the meaning colors from the issue", () => {
    expect(CAT_SELF).toBe("#6aa8ff");
    expect(LTMS_CORE).toBe("#9fdcff");
    expect(MIND).toBe("#b06bff");
    expect(RELAY).toBe("#5d7a99");
    expect(VOID).toBe("#000");
    expect(LIGHT_COLORS).toEqual({ "cat-self": "#6aa8ff", "ltms-core": "#9fdcff", mind: "#b06bff",
      relay: "#5d7a99", void: "#000" });
  });
  it("has the brightness ladder, in order", () => {
    expect(LIGHT.asleep).toBe(0.18);
    expect(LIGHT.trail).toBe(0.35);
    expect(LIGHT.breathing).toEqual({ min: 0.45, max: 0.75, rest: 0.6, periodMs: 3200 });
    expect(LIGHT.lit).toBe(0.85);
    expect(LIGHT.live).toBe(1);
    const ladder = [LIGHT.asleep, LIGHT.trail, LIGHT.breathing.min, LIGHT.breathing.max, LIGHT.lit, LIGHT.live];
    expect([...ladder].sort((a, b) => a - b)).toEqual(ladder);
  });
  it("new meaning colors don't reuse a role, category or neutral color", () => {
    const old = [...Object.values(ROLES).map((r) => r.color), ...CATEGORIES.map((c) => c.color), NEUTRAL];
    for (const c of [CAT_SELF, LTMS_CORE, MIND, VOID]) expect(old).not.toContain(c);
  });
  it("index.css mirrors the tokens, outside any theme block", () => {
    const css = readFileSync(resolve(process.cwd(), "src/index.css"), "utf8"); // vitest runs from dashboard/
    const root = css.slice(css.indexOf(":root"), css.indexOf("[data-theme=\"holo\"]"));
    for (const [name, value] of Object.entries(LIGHT_COLORS)) expect(root).toContain(`--${name}: ${value};`);
    expect(root).toContain(`--light-asleep: ${LIGHT.asleep};`);
    expect(root).toContain(`--light-trail: ${LIGHT.trail};`);
    expect(root).toContain(`--light-breathing-min: ${LIGHT.breathing.min};`);
    expect(root).toContain(`--light-breathing-max: ${LIGHT.breathing.max};`);
    expect(root).toContain(`--light-lit: ${LIGHT.lit};`);
    expect(root).toContain(`--light-live: ${LIGHT.live};`);
    expect(root).toContain("--radius-lg: 14px;");
  });
  it("frame text stays 4.5:1 on the void stage", () => {
    for (const text of ["paper", "paper-muted", "paper-faint"]) {
      expect(contrast(HOLO[text], "#000000"), text).toBeGreaterThanOrEqual(4.5);
    }
  });
});

describe("maths", () => {
  it("smoothstep", () => {
    expect(smoothstep(0, 1, -1)).toBe(0);
    expect(smoothstep(0, 1, 0)).toBe(0);
    expect(smoothstep(0, 1, 0.5)).toBe(0.5);
    expect(smoothstep(0, 1, 1)).toBe(1);
    expect(smoothstep(0, 1, 2)).toBe(1);
    expect(smoothstep(0, 10, 2.5)).toBeCloseTo(0.15625);
    expect(smoothstep(5, 5, 4)).toBe(0);
    expect(smoothstep(5, 5, 5)).toBe(1);
  });
  it("breathe stays in 0.45–0.75 over 3.2 s, and rests at 0.6 when still", () => {
    expect(breathe(0)).toBeCloseTo(0.45);
    expect(breathe(1600)).toBeCloseTo(0.75);
    expect(breathe(3200)).toBeCloseTo(0.45);
    for (let t = 0; t < 6400; t += 97) {
      expect(breathe(t)).toBeGreaterThanOrEqual(0.45 - 1e-9);
      expect(breathe(t)).toBeLessThanOrEqual(0.75 + 1e-9);
    }
    expect(breathe(1234, true)).toBe(0.6);
  });
  it("withAlpha", () => {
    expect(withAlpha("#6aa8ff", 0.5)).toBe("rgba(106, 168, 255, 0.5)");
    expect(withAlpha("#000", 0)).toBe("rgba(0, 0, 0, 0)");
    expect(withAlpha("red", 1)).toBe("red");
  });
});

describe("heartbeatSwell", () => {
  const beat = 10_000;
  it("is 0 with no beat, before the beat and after the rest", () => {
    expect(heartbeatSwell(beat, null)).toBe(0);
    expect(heartbeatSwell(beat - 1, beat)).toBe(0);
    expect(heartbeatSwell(beat, beat)).toBe(0);
    expect(heartbeatSwell(beat + 1000, beat)).toBe(0);
  });
  it("lub peaks at 1, dub 120 ms later at 0.8, with a dip between", () => {
    const { attackMs, dubDelayMs, dubLevel } = HEARTBEAT;
    expect(heartbeatSwell(beat + attackMs, beat)).toBe(1);
    expect(heartbeatSwell(beat + dubDelayMs + attackMs, beat)).toBeCloseTo(dubLevel);
    const dip = heartbeatSwell(beat + dubDelayMs + 5, beat);
    expect(dip).toBeLessThan(dubLevel);
    expect(dip).toBeGreaterThan(0);
  });
  it("stays in 0..1 everywhere", () => {
    for (let dt = -50; dt < 600; dt += 3) {
      const v = heartbeatSwell(beat + dt, beat);
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThanOrEqual(1);
    }
  });
  it("heart rate stays in the 48–60 BPM resting range", () => {
    expect(heartbeatBpm(30)).toBe(48);
    expect(heartbeatBpm(300)).toBe(48);
    expect(heartbeatBpm(1)).toBe(60);
    expect(heartbeatBpm(0.1)).toBe(60);
    expect(heartbeatBpm(10)).toBeGreaterThan(48);
    expect(heartbeatBpm(10)).toBeLessThan(60);
    expect(heartbeatBpm(NaN)).toBe(48);
  });
});

describe("flash gate", () => {
  it("allows at most 3 flashes a second per light", () => {
    const gate = createFlashGate();
    let n = 0;
    for (let t = 0; t < 1000; t += 10) if (gate.allow("a", t)) n += 1;
    expect(n).toBe(3);
    expect(gate.allow("b", 990)).toBe(true);
  });
});

function fakeCanvas() {
  const made = [];
  const make = (size) => {
    const ctx = {
      createRadialGradient: vi.fn(() => ({ addColorStop: vi.fn() })),
      fillRect: vi.fn(),
    };
    const c = { width: size, height: size, getContext: () => ctx };
    made.push(c);
    return c;
  };
  return { make, made };
}

describe("glow sprite cache", () => {
  it("draws nothing for a bad radius or color, and never makes a canvas for one (regression)", () => {
    const make = vi.fn(() => ({ width: 0, getContext: () => null }));
    for (const r of [NaN, Infinity, -Infinity, "#ff7a59", undefined, null]) expect(glowSprite("#6aa8ff", r, make)).toBeNull();
    expect(glowSprite(7, 5, make)).toBeNull();
    expect(make).not.toHaveBeenCalled();
  });

  it("caps a glow at 512 px radius", () => {
    const make = vi.fn((size) => ({ width: size, getContext: () => ({
      createRadialGradient: () => ({ addColorStop() {} }), fillRect() {} }) }));
    expect(glowSprite("#123456", 1e6, make).width).toBe(1024);
  });

  it("makes one sprite per color and whole-pixel radius", () => {
    const { make, made } = fakeCanvas();
    const a = glowSprite("#6aa8ff", 10, make);
    expect(a.width).toBe(20);
    expect(glowSprite("#6aa8ff", 10.2, make)).toBe(a);
    expect(made.length).toBe(1);
    glowSprite("#6aa8ff", 12, make);
    glowSprite("#9fdcff", 10, make);
    expect(made.length).toBe(3);
    expect(glowCacheSize()).toBe(3);
    clearGlowCache();
    expect(glowCacheSize()).toBe(0);
  });
  it("is bounded, dropping the least recently used", () => {
    const { make } = fakeCanvas();
    const first = glowSprite("#000", 1, make);
    for (let r = 2; r <= 256; r += 1) glowSprite("#000", r, make);
    expect(glowSprite("#000", 1, make)).toBe(first); // touched: now the newest
    glowSprite("#000", 999, make); // pushes out radius 2, the oldest
    expect(glowCacheSize()).toBe(256);
    expect(glowSprite("#000", 1, make)).toBe(first);
  });
  it("returns null without a 2D context and caches nothing", () => {
    expect(glowSprite("#fff", 5, () => ({ getContext: () => null }))).toBeNull();
    expect(glowCacheSize()).toBe(0);
  });
  it("drawGlow adds light with 'lighter' and restores the context", () => {
    const { make } = fakeCanvas();
    glowSprite("#6aa8ff", 8, make);
    const seen = [];
    const ctx = {
      save: vi.fn(), restore: vi.fn(),
      drawImage: vi.fn(function () { seen.push([this.globalCompositeOperation, this.globalAlpha]); }),
    };
    drawGlow(ctx, 50, 40, "#6aa8ff", 8, 0.35);
    expect(ctx.drawImage).toHaveBeenCalledWith(expect.anything(), 42, 32);
    expect(seen).toEqual([["lighter", 0.35]]);
    expect(ctx.restore).toHaveBeenCalled();
  });
});

describe("pyramid", () => {
  it("has 5 points and 8 edges, apex above the base", () => {
    const pts = pyramidPoints(40, 0.3);
    expect(pts.length).toBe(5);
    expect(PYRAMID_EDGES.length).toBe(8);
    for (const p of pts.slice(0, 4)) expect(pts[4].y).toBeLessThan(p.y);
  });
  it("turning keeps its shape (edge lengths in 3D are fixed)", () => {
    const len = (pts) => PYRAMID_EDGES.map(([a, b]) => Math.hypot(pts[a].x - pts[b].x, pts[a].y - pts[b].y, pts[a].z - pts[b].z));
    const a = len(pyramidPoints(40, 0)), b = len(pyramidPoints(40, 1.1));
    a.forEach((v, i) => expect(b[i]).toBeCloseTo(v));
  });
  it("draws 8 edges on the context", () => {
    glowSprite(LTMS_CORE, 15, fakeCanvas().make); // the apex glow (size 40, flash 0.5), so no real canvas is needed
    const ctx = { save() {}, restore() {}, beginPath() {}, moveTo: vi.fn(), lineTo() {}, stroke: vi.fn(), drawImage: vi.fn() };
    pyramid(ctx, 100, 100, 40, 2, 0.5);
    expect(ctx.stroke).toHaveBeenCalledTimes(8);
    expect(ctx.drawImage).toHaveBeenCalledTimes(1);
  });
});

describe("Calm view preferences", () => {
  it("reads prefers-reduced-motion", () => {
    window.matchMedia = vi.fn().mockReturnValue({ matches: true });
    expect(prefersReducedMotion()).toBe(true);
    window.matchMedia = vi.fn().mockReturnValue({ matches: false });
    expect(prefersReducedMotion()).toBe(false);
    delete window.matchMedia;
    expect(prefersReducedMotion()).toBe(false);
  });
  it("remembers 'Start visualizations in Calm view'", () => {
    expect(getCalmStart()).toBe(false);
    setCalmStart(true);
    expect(getCalmStart()).toBe(true);
    setCalmStart(false);
    expect(getCalmStart()).toBe(false);
  });
});
