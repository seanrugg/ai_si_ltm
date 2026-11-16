import { afterEach, describe, expect, it } from "vitest";
import { cleanup } from "@testing-library/react";
import { globeTouchables, buildGeography } from "./geography.js";
import { markerLabel, markerPath, siteRole, sourceStyle } from "./sites.js";

afterEach(() => cleanup());

describe("Holo globe markers (#347)", () => {
  it("picks the shape by role", () => {
    const roles = { h: "hub", o: "observer", m: "member" };
    const r = (n) => roles[n];
    expect(siteRole({ members: ["m", "h"] }, r)).toBe("hub");
    expect(siteRole({ members: ["o"] }, r)).toBe("observer");
    expect(siteRole({ members: ["m", "o"] }, r)).toBe("member");
    expect(markerPath("hub", 0, 0, 10).split("L")).toHaveLength(6);
    expect(markerPath("member", 0, 0, 10)).toBe("M0,-10L10,0L0,10L-10,0Z");
  });

  it("draws the location source as line style, never colour alone", () => {
    expect(sourceStyle("gps").dash).toBeUndefined();
    expect(sourceStyle("network").dash).toBe("2 2");
    expect(sourceStyle("declared").fillOpacity).toBeLessThan(1);
    expect(markerLabel({ label: "jetson-hub", source: "declared", members: ["jetson-hub"] }, "hub"))
      .toBe("jetson-hub, hub, location declared");
  });

  it("names every light on the globe with its place source (#405: the touch list replaces SVG markers)", () => {
    const g = buildGeography({
      locations: { members: [{ name: "hub-a", best: { source: "network", label: "Town", lat: 0, lon: 0, accuracy_km: 30 } }] },
      swarms: [{ name: "lab", members: [{ name: "hub-a", role: "hub" }] }],
      members: [{ name: "hub-a", kind: "relay" }],
    });
    const items = globeTouchables(g, null);
    expect(items.map((t) => t.label)).toEqual(["FLTMS", "lab · Town (estimated)"]);
    expect(items[1].narration).toMatch(/hub hub-a\. Estimated place: Town, within about 30 km/);
  });
});
