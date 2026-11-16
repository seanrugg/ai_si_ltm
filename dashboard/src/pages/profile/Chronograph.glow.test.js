// Regression (Vector's Chronology crashed in production, 15 Nov): drawGlow was
// called as (ctx, x, y, radius, color), so the radius was a color string, which
// became NaN and threw from new OffscreenCanvas(). Every call must pass a color
// string, then a finite radius.
import { beforeAll, describe, expect, it, vi } from "vitest";
import { drawChronograph, leapsOf } from "./Chronograph.jsx";
import { EPISODES } from "./episodes.fixture.js";

const glows = [];
vi.mock("../../lib/light.js", async (importOriginal) => ({
  ...(await importOriginal()),
  drawGlow: (...args) => { glows.push(args); },
}));

beforeAll(() => {
  HTMLCanvasElement.prototype.getContext = () => null;
});

function ctx() {
  return new Proxy({}, {
    get: (target, prop) => (prop in target ? target[prop] : () => ({ addColorStop() {} })),
    set: (target, prop, value) => { target[prop] = value; return true; },
  });
}

describe("Chronograph glows (#403 regression)", () => {
  it("passes a color string, then a finite radius, to every drawGlow", () => {
    const since = Date.parse(EPISODES.since);
    for (const small of [false, true]) {
      for (const hand of [0, 600, 1300, 3000]) {
        drawChronograph(ctx(), 400, 400, { data: EPISODES, hand, leapList: leapsOf(EPISODES), animate: true,
                                           leapS: 84, small, sinceMs: since, nowMs: since + hand * 1000 });
      }
    }
    expect(glows.length).toBeGreaterThan(0);
    for (const [, x, y, color, radius, alpha] of glows) {
      expect(typeof color).toBe("string");
      expect(Number.isFinite(radius) && radius > 0).toBe(true);
      expect(Number.isFinite(x) && Number.isFinite(y)).toBe(true);
      expect(alpha === undefined || Number.isFinite(alpha)).toBe(true);
    }
  });
});
