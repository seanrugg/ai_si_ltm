import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { Unplaced } from "./Network.jsx";
import { GeoStatus } from "../Settings.jsx";
import { sitesOf, unplacedOf } from "../../lib/sites.js";

afterEach(() => cleanup());

const LOCS = [
  { name: "jetson-hub", best: null, all: [], unplaced: "Only a local network address (10.0.0.50) has been seen." },
  { name: "z3-r0", best: { source: "network", lat: 1, lon: 2, accuracy_km: 20, via_hub: "jetson-hub" }, all: [] },
];

describe("unplaced members (#346)", () => {
  it("lists who isn't on the globe and why", () => {
    expect(unplacedOf(LOCS)).toEqual([{ name: "jetson-hub", reason: LOCS[0].unplaced }]);
    render(React.createElement(Unplaced, { rows: unplacedOf(LOCS), geo: { enabled: false, reason: "LTMS_GEOIP_DB isn't set" } }));
    expect(screen.getByText("Not on the globe (1)")).toBeTruthy();
    expect(screen.getByText(/10\.0\.0\.50/)).toBeTruthy();
    expect(screen.getByText(/Network estimates are off: LTMS_GEOIP_DB isn't set/)).toBeTruthy();
  });

  it("says when a position came through a hub", () => {
    const [site] = sitesOf(LOCS, [{ name: "z3-r0", kind: "robot" }]);
    expect(site.note).toBe("estimate ±20 km, via hub jetson-hub");
  });

  it("shows whether location estimates work", () => {
    render(React.createElement(GeoStatus, { geo: { enabled: false, path: null, reason: "LTMS_GEOIP_DB isn't set" } }));
    expect(screen.getByRole("status").textContent).toBe("Off: LTMS_GEOIP_DB isn't set.");
  });
});
