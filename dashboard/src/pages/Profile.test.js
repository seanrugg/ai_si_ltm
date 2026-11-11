import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import Profile from "./Profile.jsx";
import { api } from "../api/client.js";
import { dayTotal, nextTab, niceMax, rangeOf, stackDay, tabOf, wipesOf } from "../lib/profile.js";

vi.mock("../api/client.js", () => ({ api: vi.fn() }));
afterEach(() => cleanup());

const zero = { sense: 0, act: 0, comm: 0, learn: 0 };
const summary = {
  subject: { type: "member", name: "z3-r0" }, hours: 168,
  categories: { sense: 12, act: 7, comm: 3, learn: 1 }, action_success: 0.64,
  daily: [{ day: "2026-11-09", ...zero, sense: 4, act: 2 }, { day: "2026-11-10", ...zero, comm: 3, learn: 1 }],
  places: [], outcomes: {}, flow: {}, members: [], lessons: [],
  peers: [{ name: "vector", ...zero, comm: 3, learn: 1 }],
};

function mockApi() {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === "/admin/profile/summary") return summary;
    if (path === "/admin/profile/history") return { events: [
      { ts: "2026-11-10T09:00:00+00:00", kind: "admin", action: "memory_wipe", title: "Memory wiped", detail: "wipe 2" }] };
    if (path === "/admin/members") return { members: [
      { id: "1", name: "z3-r0", kind: "robot", status: "active", memory_generation: 2, last_seen_at: null }] };
    if (path === "/admin/swarms") return [{ name: "echo-base", members: [{ name: "z3-r0", role: "member" }] }];
    if (path === "/admin/locations") return { members: [{ name: "z3-r0", best: { source: "declared", label: "Lab" } }] };
    throw new Error(`unexpected ${path}`);
  });
}

let location;
function Spy() {
  location = useLocation();
  return null;
}

function renderAt(url) {
  return render(React.createElement(MemoryRouter, { initialEntries: [url] },
    React.createElement(Routes, null,
      React.createElement(Route, { path: "/members/:name/profile",
        element: React.createElement(React.Fragment, null,
          React.createElement(Profile, { subject: "member" }), React.createElement(Spy)) }))));
}

describe("pure helpers", () => {
  it("stacked segments sum to each day's total, in category order", () => {
    for (const day of summary.daily) {
      const segs = stackDay(day);
      expect(segs.map((s) => s.key)).toEqual(["sense", "act", "comm", "learn"]);
      expect(segs.at(-1).y1).toBe(dayTotal(day));
      expect(segs.reduce((sum, s) => sum + s.value, 0)).toBe(dayTotal(day));
      segs.slice(1).forEach((s, i) => expect(s.y0).toBe(segs[i].y1));
    }
  });
  it("ranges, tabs and axis maximum", () => {
    expect(rangeOf("30d").hours).toBe(720);
    expect(rangeOf("all").hours).toBe(0);
    expect(rangeOf("bogus").key).toBe("7d");
    expect(tabOf("bogus")).toBe("overview");
    expect(nextTab("overview", "ArrowLeft")).toBe("history");
    expect(nextTab("history", "ArrowRight")).toBe("overview");
    expect(niceMax(7)).toBe(10);
    expect(niceMax(0)).toBe(1);
    expect(wipesOf([{ action: "memory_wipe", ts: "t", detail: "wipe 3" }])).toEqual([{ ts: "t", generation: 3 }]);
  });
});

describe("Profile page", () => {
  it("shows the identity, tiles and peers", async () => {
    mockApi();
    renderAt("/members/z3-r0/profile");
    await screen.findByText("64% of actions succeeded");
    expect(screen.getByRole("heading", { name: "z3-r0" })).toBeDefined();
    expect(screen.getAllByText("Member · Physical AI").length).toBe(2); // the badge and the mark's label
    expect(screen.getByText(/generation 2/)).toBeDefined();
    await screen.findByText("Lab");
    expect(screen.getByRole("group", { name: "Legend" })).toBeDefined();
    expect(screen.getByRole("link", { name: "Swarm" }).getAttribute("href")).toContain("/swarms/echo-base/profile");
    expect(screen.getByText(/memory wipe · gen 2/)).toBeDefined();
  });

  it("keeps the tab and range in the URL", async () => {
    mockApi();
    renderAt("/members/z3-r0/profile?range=30d&tab=network");
    await screen.findByText("Coming soon.");
    expect(screen.getByRole("tab", { name: "Network" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("button", { name: "30d" }).getAttribute("aria-pressed")).toBe("true");
    expect(vi.mocked(api).mock.calls.find(([p]) => p === "/admin/profile/summary")[1].params.hours).toBe(720);

    fireEvent.click(screen.getByRole("button", { name: "24h" }));
    fireEvent.click(screen.getByRole("tab", { name: "Overview" }));
    await waitFor(() => expect(location.search).toContain("range=24h"));
    expect(new URLSearchParams(location.search).get("tab")).toBe("overview");
  });

  it("moves between tabs with Left and Right", async () => {
    mockApi();
    renderAt("/members/z3-r0/profile");
    const overview = await screen.findByRole("tab", { name: "Overview" });
    fireEvent.keyDown(overview, { key: "ArrowRight" });
    await waitFor(() => expect(screen.getByRole("tab", { name: "Places & movement" }).getAttribute("aria-selected")).toBe("true"));
    expect(document.activeElement.textContent).toBe("Places & movement");
    fireEvent.keyDown(document.activeElement, { key: "ArrowLeft" });
    fireEvent.keyDown(document.activeElement, { key: "ArrowLeft" });
    await waitFor(() => expect(new URLSearchParams(location.search).get("tab")).toBe("history"));
  });

  it("selecting a peer opens Interactions with it selected", async () => {
    mockApi();
    renderAt("/members/z3-r0/profile");
    fireEvent.click(await screen.findByRole("button", { name: /^vector: 4 records/ }));
    await waitFor(() => expect(new URLSearchParams(location.search).get("tab")).toBe("interactions"));
    expect(new URLSearchParams(location.search).get("peer")).toBe("vector");
  });
});
