import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { filterMembers } from "./Members.jsx";
import Members from "./Members.jsx";

const members = {
  members: [
    { id: 1, name: "alice", status: "active", kind: "agent", permissions: [], last_ack_seq: 3, last_seen_at: null },
    { id: 2, name: "bob", status: "active", kind: "agent", permissions: [], last_ack_seq: 1, last_seen_at: null },
    { id: 3, name: "charlie", status: "active", kind: "agent", permissions: [], last_ack_seq: 2, last_seen_at: null },
  ],
};
const swarms = [
  { id: 1, name: "echo-base", members: [{ name: "alice", role: "member" }, { name: "charlie", role: "member" }] },
];

vi.mock("../api/useApi.js", () => ({
  useApi: vi.fn((path) => {
    if (path === "/admin/members") return { data: members, error: null, loading: false, reload: () => {} };
    if (path === "/admin/swarms") return { data: swarms, loading: false };
    return { data: null, error: null, loading: false, reload: () => {} };
  }),
}));

const swarmOf = new Map();
for (const s of swarms) for (const m of s.members) swarmOf.set(m.name, { swarm: s.name, role: m.role });
const active = members.members.filter((m) => m.status === "active");

describe("filterMembers", () => {
  it("all returns every active member", () => {
    expect(filterMembers(active, swarmOf, "all")).toHaveLength(3);
    expect(filterMembers(active, swarmOf, undefined)).toHaveLength(3);
  });
  it("free returns only members not in a swarm", () => {
    expect(filterMembers(active, swarmOf, "free").map((m) => m.name)).toEqual(["bob"]);
  });
  it("swarm returns the members in that swarm", () => {
    expect(filterMembers(active, swarmOf, "swarm:echo-base").map((m) => m.name)).toEqual(["alice", "charlie"]);
  });
  it("an unknown swarm returns none", () => {
    expect(filterMembers(active, swarmOf, "swarm:nope")).toEqual([]);
  });
});

describe("show parsed from the hash query", () => {
  it("applies the free filter when the hash carries show=free", () => {
    const html = renderToStaticMarkup(
      React.createElement(MemoryRouter, { initialEntries: ["/members?show=free"] }, React.createElement(Members))
    );
    expect(html).toContain("Free agents (1)");
  });
  it("applies the swarm filter when the hash carries show=swarm:echo-base", () => {
    const html = renderToStaticMarkup(
      React.createElement(MemoryRouter, { initialEntries: ["/members?show=swarm:echo-base"] }, React.createElement(Members))
    );
    expect(html).toContain("echo-base (2)");
  });
});
