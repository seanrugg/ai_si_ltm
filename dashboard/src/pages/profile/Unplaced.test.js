import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { GeographyTables } from "./Network.jsx";
import { GeoStatus } from "../Settings.jsx";
import { sitesOf, unplacedOf } from "../../lib/sites.js";
import { buildGeography } from "../../lib/geography.js";

afterEach(() => cleanup());

const LOCS = [
  { name: "jetson-hub", best: null, all: [], unplaced: "Only a local network address (10.0.0.50) has been seen." },
  { name: "z3-r0", best: { source: "network", lat: 1, lon: 2, accuracy_km: 20, via_hub: "jetson-hub" }, all: [] },
];

describe("unplaced members (#346)", () => {
  it("lists who isn't on the globe and why, without the address (#405)", () => {
    expect(unplacedOf(LOCS)).toEqual([{ name: "jetson-hub", reason: LOCS[0].unplaced }]);
    const locations = { members: LOCS, geo: { enabled: false, reason: "LTMS_GEOIP_DB isn't set" }, server: null };
    const geo = buildGeography({ locations, members: [{ name: "jetson-hub", kind: "relay" }, { name: "z3-r0", kind: "robot" }] });
    render(React.createElement(GeographyTables, { geo, server: null, events: [], t: 0, locations, network: [], members: [] }));
    expect(screen.getByText("Unplaced (1)")).toBeTruthy();
    expect(screen.getByText("Only a local network address has been seen.")).toBeTruthy();
    expect(screen.queryByText(/10\.0\.0\.50/)).toBeNull(); // addresses are only in "Members and addresses"
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
