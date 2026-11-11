import { describe, expect, it } from "vitest";
import { arc, distanceDeg, invert, project, zoomAt } from "./globe.js";

const R = 100;
const C = 150;

describe("orthographic projection", () => {
  it("project then invert round-trips a visible point", () => {
    const rot = { lon: -77, lat: 38 };
    for (const [lon, lat] of [[-77, 38], [-70, 40], [-100, 20], [10, 50]]) {
      const p = project(lon, lat, rot, R, C, C);
      expect(p.visible).toBe(true);
      const back = invert(p.x, p.y, rot, R, C, C);
      expect(back.lon).toBeCloseTo(lon, 6);
      expect(back.lat).toBeCloseTo(lat, 6);
    }
  });
  it("a point on the far side is not visible, and off-disc inverts to null", () => {
    expect(project(103, -38, { lon: -77, lat: 38 }, R, C, C).visible).toBe(false);
    expect(invert(C + R + 1, C, { lon: 0, lat: 0 }, R, C, C)).toBeNull();
  });
  it("arcs start and end on their sites, raised in the middle", () => {
    const pts = arc({ lon: -77, lat: 38 }, { lon: 2, lat: 48 }, 0.2);
    expect(pts[0].alt).toBeCloseTo(0);
    expect(pts.at(-1).lat).toBeCloseTo(48, 4);
    expect(Math.max(...pts.map((p) => p.alt))).toBeCloseTo(0.2, 2);
  });
});

describe("double-click zoom", () => {
  it("centres on the inverted point and doubles the zoom", () => {
    const view = { lon: 0, lat: 0, zoom: 1 };
    const p = project(20, 10, view, R, C, C);
    const out = zoomAt(p.x, p.y, view, R, C, C);
    expect(out.home).toBe(false);
    expect(out.view.lon).toBeCloseTo(20, 6);
    expect(out.view.lat).toBeCloseTo(10, 6);
    expect(out.view.zoom).toBe(2);
    expect(zoomAt(p.x, p.y, { ...view, zoom: 6 }, R, C, C).view.zoom).toBe(8); // capped
  });
  it("near home at zoom 6+ asks for the Home view", () => {
    const home = { lon: -77, lat: 38 };
    const view = { lon: -77, lat: 38, zoom: 6 };
    expect(zoomAt(C, C, view, R * 6, C, C, home)).toEqual({ view, home: true });
    expect(distanceDeg(home, { lon: -77, lat: 44 })).toBeCloseTo(6, 6);
  });
});
