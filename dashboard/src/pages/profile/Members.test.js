import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import Profile from "../Profile.jsx";
import { api } from "../../api/client.js";
import { nextTab, swarmCount, tabOf, tabsFor } from "../../lib/profile.js";

vi.mock("../../api/client.js", () => ({ api: vi.fn() }));
afterEach(() => cleanup());

const zero = { sense: 0, act: 0, comm: 0, learn: 0 };
const swarms = [{ name: "echo-base", status: "active", members: [
  { name: "z3-r0", role: "member" }, { name: "hub-one", role: "hub" }, { name: "pi", role: "observer" }] }];

function mockApi() {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === "/admin/profile/summary") return {
      subject: { type: "swarm", name: "echo-base" }, hours: 168, categories: zero, daily: [], places: [],
      outcomes: {}, flow: {}, lessons: [], peers: [], members: [{ name: "z3-r0", ...zero, sense: 5, act: 2 }] };
    if (path === "/admin/profile/history") return { events: [] };
    if (path === "/admin/members") return { members: [
      { name: "z3-r0", kind: "robot", status: "active", last_seen_at: null },
      { name: "hub-one", kind: "relay", status: "active", last_seen_at: null },
      { name: "pi", kind: "robot", status: "active", last_seen_at: null },
      { name: "loose", kind: "agent", status: "active", last_seen_at: null }] };
    if (path === "/admin/swarms") return swarms;
    throw new Error(`unexpected ${path}`);
  });
}

function renderSwarm(tab) {
  return render(React.createElement(MemoryRouter, { initialEntries: [`/swarms/echo-base/profile?tab=${tab}`] },
    React.createElement(Routes, null,
      React.createElement(Route, { path: "/swarms/:name/profile",
        element: React.createElement(Profile, { subject: "swarm" }) }))));
}

describe("swarm Members tab (#282)", () => {
  it("only swarm profiles have the Members tab", () => {
    expect(tabsFor("swarm").map((t) => t.key)).toContain("members");
    expect(tabsFor("member").map((t) => t.key)).not.toContain("members");
    expect(tabOf("members", "member")).toBe("overview");
    expect(tabOf("members", "swarm")).toBe("members");
    expect(nextTab("overview", "ArrowRight", "swarm")).toBe("members");
    expect(nextTab("overview", "ArrowRight", "member")).toBe("specs");  // Specs follows Overview (#333)
  });

  it("the header count says what it counts", () => {
    expect(swarmCount(swarms[0].members)).toBe("1 hub · 1 member · 1 observer");
    expect(swarmCount([{ role: "hub" }])).toBe("1 hub");
    expect(swarmCount([{ role: "member" }, { role: "member" }])).toBe("2 members");
    expect(swarmCount([])).toBe("no members yet");
  });

  it("lists members with role, kind and records, manages non-hub rows, and offers Add", async () => {
    mockApi();
    renderSwarm("members");
    const table = await screen.findByRole("table", { name: /Members of echo-base/ });
    const rows = within(table).getAllByRole("row").slice(1).map((r) => r.textContent);
    expect(rows[0]).toContain("hub-one");               // hub first
    expect(rows[0]).toContain("Change the hub with Appoint hub");
    expect(rows[1]).toContain("z3-r0");
    expect(rows[1]).toContain("7");                     // records in range
    expect(screen.queryByRole("button", { name: "Remove hub-one" })).toBeNull();
    expect(screen.getByRole("button", { name: "Remove z3-r0" })).toBeDefined();
    expect(screen.getByRole("link", { name: "z3-r0" }).getAttribute("href")).toBe("/members/z3-r0/profile");
    expect(screen.getByText("Swarm · 1 hub · 1 member · 1 observer · No leader")).toBeDefined();
    const add = screen.getByRole("form", { name: "Add member" });
    expect(within(add).getByRole("option", { name: "loose" })).toBeDefined();
  });
});
