import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import Geography from "./Network.jsx";
import { api } from "../../api/client.js";
import { tabOf } from "../../lib/profile.js";
import { createGlobeMotion } from "./Globe.jsx";
import { travelMs } from "../../lib/topology.js";

vi.mock("../../api/client.js", () => ({ api: vi.fn() }));
afterEach(() => cleanup());

const SINCE = "2026-11-14T00:00:00Z";
const SWARMS = [{ name: "lab", members: [{ name: "hub-1", role: "hub" }, { name: "rover-1", role: "member" },
  { name: "helper", role: "member" }, { name: "lost", role: "member" }] }];
const MEMBERS = [{ name: "hub-1", kind: "relay" }, { name: "rover-1", kind: "robot" }, { name: "helper", kind: "agent" },
  { name: "lost", kind: "robot" }];

function mockApi({ attribution = false } = {}) {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockImplementation(async (path, opts) => {
    if (path === "/admin/locations") {
      return {
        members: [
          { name: "hub-1", best: { source: "network", label: "Town", lat: 10, lon: 20, accuracy_km: 25 }, all: [] },
          { name: "rover-1", best: { source: "network", label: "Town", lat: 10, lon: 20, accuracy_km: 25, via_hub: "hub-1" }, all: [] },
          { name: "helper", best: null, all: [], unplaced: "Its address (203.0.113.7) isn't in the city database." },
          { name: "lost", best: null, all: [], unplaced: "Only a local network address (10.0.0.50) has been seen." },
        ],
        geo: { enabled: true, attribution, credit_url: "https://db-ip.com" },
        server: { source: "declared", label: "Server room", lat: 30, lon: 0 },
      };
    }
    if (path === "/admin/network") {
      return { members: [{ name: "hub-1", role: "hub", path: "direct", seen: { ip: "198.51.100.4" }, reported: [] },
        { name: "rover-1", role: "member", path: "via hub-1", seen: null, reported: [{ ip: "10.0.0.9", source: "self" }] }] };
    }
    if (path === "/admin/profile/episodes") {
      const m = opts.params.member;
      return { since: SINCE, lanes: {}, heartbeat: { interval_s: 30, last_t: 50 },
        pulses: m === "rover-1" ? [[10, 1.5, "perception", "sense", "camera", "saw a face"], [20, 0.5, "action", "act", "hub", "drove"]]
          : m === "hub-1" ? [[15, 0.2, "communication", "comm", "hub", "relayed"]] : [] };
    }
    if (path === "/admin/timeline") return { records: [] };
    throw new Error(path);
  });
}

function renderTab(url = "/swarms/lab/profile?tab=geography", props = {}) {
  return render(React.createElement(MemoryRouter, { initialEntries: [url] },
    React.createElement(Geography, {
      subject: "swarm", name: "lab", range: { key: "7d", hours: 168 }, members: MEMBERS, swarms: SWARMS,
      summary: { places: [{ place: "kitchen", total: 3, sense: 3, act: 0, comm: 0, learn: 0, action_success: null }] },
      ...props,
    })));
}

function touchList() {
  return screen.getByRole("list", { name: "Geography lights" });
}

beforeEach(() => mockApi());

describe("Geography tab (#405)", () => {
  it("replaces Network and Places & movement; old links keep working", () => {
    expect(tabOf("geography", "swarm")).toBe("geography");
    expect(tabOf("network", "swarm")).toBe("geography");
    expect(tabOf("places", "member")).toBe("geography");
  });

  it("lists the controls and every light, with no address anywhere", async () => {
    renderTab();
    const list = await screen.findByRole("list", { name: "Geography lights" });
    const labels = within(list).getAllByRole("button").map((b) => b.textContent);
    expect(labels).toEqual(["Zoom in", "Zoom out", "Into lab", "Whole globe", "LTMS", "lab · Town (estimated)",
      "helper (satellite)", "lost (unplaced)"]);
    expect(list.textContent).not.toMatch(/\d+\.\d+\.\d+\.\d+/);
    fireEvent.click(within(list).getByRole("button", { name: "lost (unplaced)" }));
    const narration = document.querySelector(".viz-narrate").textContent;
    expect(narration).toBe("lost is unplaced: Only a local network address has been seen.");
    fireEvent.click(within(list).getByRole("button", { name: "lab · Town (estimated)" }));
    // The narration line speaks at most once every 2 s.
    await vi.waitFor(() => expect(document.querySelector(".viz-narrate").textContent)
      .toMatch(/Estimated place: Town, within about 25 km/), { timeout: 3000 });
  });

  it("shows the DB-IP credit only while attribution is on", async () => {
    renderTab();
    await screen.findByRole("list", { name: "Geography lights" });
    expect(screen.queryByRole("link", { name: "IP geolocation by DB-IP" })).toBeNull();
    cleanup();
    mockApi({ attribution: true });
    renderTab();
    await screen.findByRole("list", { name: "Geography lights" });
    const legend = screen.getByRole("group", { name: "Legend" });
    expect(within(legend).getByRole("link", { name: "IP geolocation by DB-IP" })).toBeDefined();
  });

  it("Calm view puts the table twin first: places, unplaced, members and addresses", async () => {
    renderTab();
    await screen.findByRole("list", { name: "Geography lights" });
    fireEvent.click(screen.getByRole("button", { name: "Table" }));
    const twin = screen.getByTestId("table-twin");
    const places = within(twin).getByRole("table", { name: "Places" });
    const rows = within(places).getAllByRole("row").slice(1).map((r) => [...r.cells].map((c) => c.textContent));
    expect(rows.map((r) => r.slice(0, 3))).toEqual([
      ["lab · Town", "hub-1, rover-1", "network, estimated (±25 km)"],
      ["Cloud (satellites)", "helper", "provider's place"],
      ["LTMS", "—", "declared"],
    ]);
    const unplaced = within(twin).getByRole("table", { name: "Unplaced (1)" });
    expect(unplaced.textContent).toContain("Only a local network address has been seen.");
    expect(unplaced.textContent).not.toContain("10.0.0.50");
    // Addresses are only in their own table.
    const addresses = within(twin).getByRole("table", { name: "Members and addresses" });
    expect(addresses.textContent).toContain("198.51.100.4");
    expect(within(twin).getByRole("table", { name: "Records by place" }).textContent).toContain("kitchen");
    // The still view follows the twin.
    expect(twin.compareDocumentPosition(screen.getByTestId("globe")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("?tab=places opens zoomed into the swarm: the star system's lights", async () => {
    renderTab("/members/rover-1/profile?tab=places", { subject: "member", name: "rover-1" });
    const list = await screen.findByRole("list", { name: "Geography lights" });
    const labels = within(list).getAllByRole("button").map((b) => b.textContent);
    expect(labels).toContain("hub-1");
    expect(labels).toContain("rover-1");
    expect(labels).not.toContain("helper (satellite)");
  });

  it("Escape goes one level out of a swarm", async () => {
    renderTab("/members/rover-1/profile?tab=places", { subject: "member", name: "rover-1" });
    const list = await screen.findByRole("list", { name: "Geography lights" });
    fireEvent.keyDown(list, { key: "Escape" });
    // Reduced motion in tests (no animation frames): the move lands at once on the next level poll.
    await vi.waitFor(() => expect(within(touchList()).queryByRole("button", { name: "rover-1" })).toBeNull(),
      { timeout: 2000 });
    expect(within(touchList()).getByRole("button", { name: "Into lab" })).toBeDefined();
  });

  it("streams each record to LTMS for its measured transit; self-reports beat instead", () => {
    const events = [{ t: 1000, member: "rover-1", category: "sense", transit: 4 },
      { t: 2000, member: "rover-1", category: "self", transit: 0 }];
    const m = createGlobeMotion(events);
    const from = () => ({ kind: "light", id: "x" });
    m.advance(0, 0, { animate: true, from }); // primes at the hand
    m.advance(2500, 100, { animate: true, from });
    expect(m.flights).toHaveLength(1);
    expect(m.flights[0].life).toBe(travelMs(4));
    expect(m.lastSelf["rover-1"]).toBe(2000);
    m.advance(2600, 100 + travelMs(4), { animate: true, from });
    expect(m.flights).toHaveLength(0); // landed on the pyramid
    // Calm view: no travel, one brightness step.
    const calm = createGlobeMotion(events);
    calm.advance(0, 0, { animate: false, from });
    calm.advance(2500, 100, { animate: false, from });
    expect(calm.flights).toHaveLength(0);
    expect(calm.stepUntil).toBe(1100);
  });
});
