import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import Interactions from "./Interactions.jsx";
import { api } from "../../api/client.js";
import { tabOf } from "../../lib/profile.js";
import { sound } from "../../lib/sound.js";

vi.mock("../../api/client.js", () => ({ api: vi.fn() }));
beforeEach(() => {
  window.matchMedia = vi.fn().mockReturnValue({ matches: false });
});
afterEach(() => {
  cleanup();
  delete window.matchMedia;
  vi.restoreAllMocks();
});

const graph = {
  nodes: [
    { name: "z3-r0", role: "member", kind: "robot", cognition: null, is_hub: false, swarm: "echo-base" },
    { name: "vector", role: "free", kind: "robot", cognition: { value: "offboard" }, is_hub: false, swarm: null },
    { name: "jetson-hub", role: "hub", kind: "relay", cognition: null, is_hub: true, swarm: "echo-base" },
  ],
  edges: [{ a: "vector", b: "z3-r0", category: "comm", count: 12 }],
  relays: [{ hub: "jetson-hub", member: "z3-r0", count: 30 }],
};

const SINCE = "2026-11-13T00:00:00+00:00";
const episodes = {
  "z3-r0": { since: SINCE, until: "2026-11-14T00:00:00+00:00", count: 4, truncated: false,
    pulses: [[10, 0.4, "perception", "sense", "camera", "saw a face"], [20, 0.2, "perception", "sense", "camera", "saw a face"],
      [30, 1.0, "perception", "self", "battery", "health"], [40, 2.0, "communication", "comm", "llm", "hi"]],
    lanes: { mind: [] }, heartbeat: { interval_s: 30, last_t: 30 } },
  "jetson-hub": { since: SINCE, count: 1, pulses: [[5, 0.1, "system", null, "hub", "started"]], lanes: {}, heartbeat: {} },
};

const swarms = [{ name: "echo-base", members: [{ name: "z3-r0", role: "member" }, { name: "jetson-hub", role: "hub" }] }];
const members = [{ id: "1", name: "z3-r0", kind: "robot", last_seen_at: null },
  { id: "2", name: "jetson-hub", kind: "relay", last_seen_at: null }];

function mockApi() {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockImplementation(async (path, { params } = {}) => {
    if (path === "/admin/profile/graph") return graph;
    if (path === "/admin/profile/episodes") return episodes[params.member];
    if (path === "/admin/network") return { members: [] };
    if (path.startsWith("/admin/members/")) return { specs: { models: { value: [] } } };
    throw new Error(`unexpected ${path}`);
  });
}

function renderTab(props = {}, url = "/") {
  return render(React.createElement(MemoryRouter, { initialEntries: [url] },
    React.createElement(Interactions, { subject: "member", name: "z3-r0", range: { hours: 168 }, members, swarms, ...props })));
}

describe("Topology tab", () => {
  it("frames the star system, with touchable lights named in the narration", async () => {
    mockApi();
    renderTab({ subject: "swarm", name: "echo-base" });
    expect(await screen.findByRole("heading", { name: "Topology" })).toBeDefined();
    const lights = screen.getByRole("list", { name: "Topology lights" });
    const labels = within(lights).getAllByRole("button").map((b) => b.textContent);
    expect(labels).toEqual(["FLTMS", "jetson-hub", "z3-r0", "z3-r0 (camera)", "z3-r0 (battery)"]);
    fireEvent.click(within(lights).getByRole("button", { name: "z3-r0" }));
    expect(await screen.findByText("z3-r0, a member planet around jetson-hub, beating at 48 bpm.")).toBeDefined();
    const calls = vi.mocked(api).mock.calls.filter(([p]) => p === "/admin/profile/episodes").map(([, o]) => o.params.member);
    expect(calls.sort()).toEqual(["jetson-hub", "z3-r0"]);
  });

  it("touch plays the shared engine's note for the light (#428)", async () => {
    mockApi();
    const note = vi.spyOn(sound, "note");
    renderTab({ subject: "swarm", name: "echo-base" });
    const lights = await screen.findByRole("list", { name: "Topology lights" });
    fireEvent.click(within(lights).getByRole("button", { name: "z3-r0" }));
    fireEvent.click(within(lights).getByRole("button", { name: "FLTMS" }));
    expect(note.mock.calls.map(([k]) => k)).toEqual(["planet:z3-r0", "ltms"]);
  });

  it("the table twin's counts match /profile/episodes", async () => {
    mockApi();
    renderTab();
    fireEvent.click(await screen.findByRole("button", { name: "Table" }));
    const twin = screen.getByTestId("table-twin");
    // The member profile reads only the member's own records.
    const bySource = within(twin).getByText("By source").closest("table");
    const counts = within(bySource).getAllByRole("row").slice(1).map((r) => r.lastChild.textContent);
    expect(counts.map(Number).reduce((a, b) => a + b, 0)).toBe(episodes["z3-r0"].count);
    expect(within(bySource).getByText("camera")).toBeDefined();
    const members_ = within(twin).getByText("Members").closest("table");
    expect(within(members_).getByText("is the hub")).toBeDefined();
    expect(within(twin).getByText("Paths")).toBeDefined();
  });

  it("keeps the earlier graph as the twin's Exchanges section", async () => {
    mockApi();
    renderTab();
    fireEvent.click(await screen.findByRole("button", { name: "Table" }));
    const vector = await screen.findByRole("link", { name: "vector, Free agent · Hybrid" });
    fireEvent.click(vector);
    expect(await screen.findByRole("link", { name: "Open conversation" })).toBeDefined();
    expect(screen.getByRole("link", { name: "Open conversation" }).getAttribute("href"))
      .toBe("/conversation?members=z3-r0%2Cvector");
    expect(screen.getByText(/Each thread is one data type/)).toBeDefined();

    fireEvent.click(screen.getByRole("button", { name: "View as table" }));
    expect(screen.getByText("vector ↔ z3-r0")).toBeDefined();
    expect(screen.getByText("jetson-hub relays for z3-r0")).toBeDefined();
  });

  it("opens on the twin when a peer is selected", async () => {
    mockApi();
    renderTab({}, "/?peer=vector");
    expect(await screen.findByTestId("table-twin")).toBeDefined();
  });
});

describe("tab keys", () => {
  it("Topology replaces Interactions, and old links keep working", () => {
    expect(tabOf("topology", "member")).toBe("topology");
    expect(tabOf("interactions", "member")).toBe("topology");
    expect(tabOf("interactions", "swarm")).toBe("topology");
  });
});
