// Fleet Geography (#382): the whole fleet on the #405 globe, with a swarm filter.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import { createMemoryRouter, MemoryRouter, RouterProvider } from "react-router-dom";
import Geography, { FREE_AGENTS, filterFleet } from "./Geography.jsx";
import { PAGE_ROUTES } from "../App.jsx";
import { api } from "../api/client.js";
import { buildGeography, globeTouchables, placeRows } from "../lib/geography.js";

vi.mock("../api/client.js", () => ({
  api: vi.fn(), ApiError: class extends Error {}, setUnauthorizedHandler: () => {},
}));
afterEach(() => cleanup());

const SWARMS = [
  { name: "lab", members: [{ name: "hub-1", role: "hub" }, { name: "rover-1", role: "member" }] },
  { name: "field", members: [{ name: "scout", role: "hub" }, { name: "lost", role: "member" }] },
];
const MEMBERS = [{ name: "hub-1", kind: "relay" }, { name: "rover-1", kind: "robot" }, { name: "scout", kind: "robot" },
  { name: "lost", kind: "robot" }, { name: "solo", kind: "robot" }, { name: "helper", kind: "agent" }];
const LOCATIONS = {
  members: [
    { name: "hub-1", best: { source: "network", label: "Town", lat: 10, lon: 20, accuracy_km: 25 }, all: [] },
    { name: "rover-1", best: { source: "network", label: "Town", lat: 10, lon: 20, accuracy_km: 25 }, all: [] },
    { name: "scout", best: { source: "declared", label: "Field site", lat: -5, lon: 40 }, all: [] },
    { name: "lost", best: null, all: [], unplaced: "Only a local network address (10.0.0.50) has been seen." },
    { name: "solo", best: { source: "gps", label: "Harbour", lat: 50, lon: -3 }, all: [] },
    { name: "helper", best: null, all: [], unplaced: "Its address (203.0.113.7) isn't in the city database." },
  ],
  geo: { enabled: true },
  server: { source: "declared", label: "Server room", lat: 30, lon: 0 },
};
const NETWORK = { members: [{ name: "hub-1", role: "hub", path: "direct", seen: { ip: "198.51.100.4" }, reported: [] }] };

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === "/admin/locations") return LOCATIONS;
    if (path === "/admin/network") return NETWORK;
    if (path === "/admin/members") return { members: MEMBERS };
    if (path === "/admin/swarms") return SWARMS;
    if (path === "/admin/profile/episodes") return { since: "2026-11-14T00:00:00Z", lanes: {}, pulses: [], heartbeat: null };
    throw new Error(path);
  });
});

function renderPage(url = "/geography") {
  return render(React.createElement(MemoryRouter, { initialEntries: [url] }, React.createElement(Geography)));
}

const lightLabels = (list) => within(list).getAllByRole("button").map((b) => b.textContent);
const IP = /\d+\.\d+\.\d+\.\d+/;

describe("Fleet Geography page (#382)", () => {
  it("has its nav route; /map goes there", async () => {
    expect(PAGE_ROUTES.map((r) => r.path)).toContain("/geography");
    const router = createMemoryRouter(PAGE_ROUTES, { initialEntries: ["/map"] });
    render(React.createElement(RouterProvider, { router }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/geography"));
  });

  it("puts every placed member on the globe and lists the rest as unplaced, with no address", async () => {
    renderPage();
    const list = await screen.findByRole("list", { name: "Geography lights" });
    expect(lightLabels(list)).toEqual(["Zoom in", "Zoom out", "Into field", "Into lab", "Whole globe", "FLTMS",
      "field · Field site", "lab · Town (estimated)", "solo · Harbour", "helper (satellite)", "lost (unplaced)"]);
    expect(list.textContent).not.toMatch(IP);

    fireEvent.click(screen.getByRole("button", { name: "Table" }));
    const twin = screen.getByTestId("table-twin");
    const places = within(twin).getByRole("table", { name: "Places" });
    const members = within(places).getAllByRole("row").slice(1).map((r) => r.cells[1].textContent)
      .flatMap((s) => s.split(", ")).filter((s) => s !== "—");
    expect(members.sort()).toEqual(["helper", "hub-1", "rover-1", "scout", "solo"]);
    const unplaced = within(twin).getByRole("table", { name: "Unplaced (1)" });
    expect(within(unplaced).getAllByRole("row").slice(1).map((r) => r.cells[0].textContent)).toEqual(["lost"]);
    expect(unplaced.textContent).not.toMatch(IP);
  });

  it("the table twin matches the markers: one Places row per light, plus satellites and FLTMS", async () => {
    renderPage();
    await screen.findByRole("list", { name: "Geography lights" });
    fireEvent.click(screen.getByRole("button", { name: "Table" }));
    const places = within(screen.getByTestId("table-twin")).getByRole("table", { name: "Places" });
    const sites = within(places).getAllByRole("row").slice(1).map((r) => r.cells[0].textContent);
    const geo = buildGeography({ locations: LOCATIONS, swarms: SWARMS, members: MEMBERS });
    const lights = globeTouchables(geo, LOCATIONS.server).filter((t) => geo.lights.some((l) => l.id === t.id));
    expect(sites).toEqual(placeRows(geo, LOCATIONS.server).map((r) => r.site));
    expect(sites.slice(0, lights.length)).toEqual(lights.map((t) => t.label.replace(" (estimated)", "")));
    expect(within(places).getByText("network, estimated (±25 km)")).toBeDefined();
  });

  it("filters to one swarm, or to the free agents", async () => {
    renderPage("/geography?swarm=field");
    let list = await screen.findByRole("list", { name: "Geography lights" });
    expect(lightLabels(list)).toEqual(["Zoom in", "Zoom out", "Into field", "Whole globe", "FLTMS",
      "field · Field site", "lost (unplaced)"]);
    fireEvent.change(screen.getByRole("combobox", { name: "Swarm" }), { target: { value: FREE_AGENTS } });
    await waitFor(() => expect(lightLabels(screen.getByRole("list", { name: "Geography lights" })))
      .toEqual(["Zoom in", "Zoom out", "Whole globe", "FLTMS", "solo · Harbour", "helper (satellite)"]));
    fireEvent.change(screen.getByRole("combobox", { name: "Swarm" }), { target: { value: "" } });
    list = await screen.findByRole("list", { name: "Geography lights" });
    await waitFor(() => expect(lightLabels(screen.getByRole("list", { name: "Geography lights" })))
      .toContain("lab · Town (estimated)"));
  });

  it("filterFleet trims locations, addresses and episodes to the chosen swarm", () => {
    const data = { locations: LOCATIONS, network: NETWORK.members, episodes: { "hub-1": {}, scout: {} } };
    const out = filterFleet(data, SWARMS, "field");
    expect(out.swarms.map((s) => s.name)).toEqual(["field"]);
    expect(out.data.locations.members.map((m) => m.name)).toEqual(["scout", "lost"]);
    expect(out.data.locations.server).toBe(LOCATIONS.server);
    expect(out.data.network).toEqual([]);
    expect(Object.keys(out.data.episodes)).toEqual(["scout"]);
    expect(filterFleet(data, SWARMS, "").data).toBe(data);
    expect(filterFleet(data, SWARMS, FREE_AGENTS).data.locations.members.map((m) => m.name)).toEqual(["solo", "helper"]);
  });

  it("a touched light opens its data card: keyboard focus moves in, Escape closes, links go to profiles", async () => {
    renderPage();
    const list = await screen.findByRole("list", { name: "Geography lights" });
    const button = within(list).getByRole("button", { name: "lab · Town (estimated)" });
    button.focus();
    fireEvent.click(button);
    const card = await screen.findByRole("dialog", { name: "lab · Town" });
    expect(card.contains(document.activeElement)).toBe(true);
    expect(within(card).getByRole("link", { name: "Open lab's profile" }).getAttribute("href"))
      .toBe("/swarms/lab/profile?tab=geography");
    expect(within(card).getByRole("link", { name: "rover-1" }).getAttribute("href"))
      .toBe("/members/rover-1/profile?tab=geography");
    expect(card.textContent).toContain("network, estimated (±25 km)");
    expect(card.textContent).not.toMatch(IP);
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("an unplaced member's card gives the reason without the address", async () => {
    renderPage();
    const list = await screen.findByRole("list", { name: "Geography lights" });
    fireEvent.click(within(list).getByRole("button", { name: "lost (unplaced)" }));
    const card = await screen.findByRole("dialog", { name: "lost" });
    expect(card.textContent).toContain("unplaced: Only a local network address has been seen.");
    expect(card.textContent).not.toMatch(IP);
    expect(within(card).getByRole("link", { name: "field" }).getAttribute("href")).toBe("/swarms/field/profile?tab=geography");
    expect(within(card).getByRole("link", { name: "Open lost's profile" })).toBeDefined();
    fireEvent.click(within(card).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
