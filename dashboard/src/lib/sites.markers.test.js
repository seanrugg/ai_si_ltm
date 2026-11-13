import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import Globe, { FLOW_STYLE } from "../pages/profile/Globe.jsx";
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

  it("makes every marker focusable and named, and turns motion off on request", () => {
    let picked = null;
    render(React.createElement(Globe, {
      view: { lon: 0, lat: 0, zoom: 1 }, setView: () => {}, rates: {}, home: null, server: null, onHome: () => {},
      sites: [{ key: "a", label: "jetson-hub", lon: 0, lat: 0, source: "declared", members: ["jetson-hub"] }],
      roleOf: () => "hub", onSelectSite: (s) => { picked = s; },
    }));
    const marker = screen.getByRole("button", { name: "jetson-hub, hub, location declared" });
    expect(marker.getAttribute("tabindex")).toBe("0");
    fireEvent.keyDown(marker, { key: "Enter" });
    expect(picked.key).toBe("a");
    expect(FLOW_STYLE).toMatch(/prefers-reduced-motion: reduce\) \{ \.ltms-flow, \.ltms-pulse, \.ltms-sweep \{ animation: none/);
  });
});
