import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";

afterEach(() => cleanup());
import { filterMembers, default as Members } from "./Members.jsx";
import { api } from "../api/client.js";

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

let allMembers;
let bulkResults;
vi.mock("../api/useApi.js", () => ({
  useApi: vi.fn((path) => {
    if (path === "/admin/members") return { data: { members: allMembers }, error: null, loading: false, reload: () => {} };
    if (path === "/admin/swarms") return { data: swarms, loading: false };
    return { data: null, error: null, loading: false, reload: () => {} };
  }),
}));

vi.mock("../api/client.js", () => ({
  api: vi.fn(),
}));

beforeEach(() => {
  allMembers = [
    { id: 1, name: "alice", status: "active", kind: "agent", permissions: [], last_ack_seq: 3, last_seen_at: null },
    { id: 2, name: "bob", status: "active", kind: "agent", permissions: [], last_ack_seq: 1, last_seen_at: null },
    { id: 3, name: "charlie", status: "active", kind: "agent", permissions: [], last_ack_seq: 2, last_seen_at: null },
    { id: "4", name: "dora", status: "pending", kind: "agent", permissions: [], requested_permissions: ["recall"], public_key: "aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111", created_at: "2026-01-01T00:00:00Z" },
    { id: "5", name: "eve", status: "pending", kind: "agent", permissions: [], requested_permissions: ["recall"], public_key: "bbbb2222bbbb2222bbbb2222bbbb2222bbbb2222bbbb2222bbbb2222bbbb2222", created_at: "2026-01-02T00:00:00Z" },
  ];
  bulkResults = [{ id: "4", ok: true }, { id: "5", ok: true }];
  vi.mocked(api).mockResolvedValue({ results: bulkResults });
});

function renderMembers() {
  return render(React.createElement(MemoryRouter, null, React.createElement(Members)));
}

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

describe("pending bulk actions", () => {
  it("renders a checkbox per pending card and a select all", () => {
    renderMembers();
    expect(screen.getByRole("checkbox", { name: /Select dora/i })).toBeDefined();
    expect(screen.getByRole("checkbox", { name: /Select eve/i })).toBeDefined();
    expect(screen.getByRole("checkbox", { name: /Select all/i })).toBeDefined();
  });

  it("shows each pending card's name and key fingerprint", () => {
    renderMembers();
    expect(screen.getAllByText("dora").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("eve").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/aaaa1111/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/bbbb2222/).length).toBeGreaterThanOrEqual(1);
  });

  it("shows approve/reject buttons once members are selected", () => {
    renderMembers();
    expect(screen.queryByText(/Approve selected/)).toBeNull();
    expect(screen.queryByText(/Reject selected/)).toBeNull();
    fireEvent.click(screen.getByRole("checkbox", { name: /Select dora/i }));
    expect(screen.getByText(/Approve selected \(1\)/));
    expect(screen.getByText(/Reject selected \(1\)/));
  });

  it("the confirm step lists each name and key fingerprint", () => {
    renderMembers();
    fireEvent.click(screen.getByRole("checkbox", { name: /Select dora/i }));
    fireEvent.click(screen.getByRole("checkbox", { name: /Select eve/i }));
    fireEvent.click(screen.getByText(/Reject selected \(2\)/));
    expect(screen.getByText(/Reject these members?/));
    expect(screen.getAllByText(/aaaa1111/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/bbbb2222/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByPlaceholderText(/Why are you rejecting these requests/));
  });

  it("approving selected shows per-id results as text and clears selection", async () => {
    renderMembers();
    fireEvent.click(screen.getByRole("checkbox", { name: /Select dora/i }));
    fireEvent.click(screen.getByRole("checkbox", { name: /Select eve/i }));
    fireEvent.click(screen.getByText(/Approve selected \(2\)/));
    fireEvent.click(screen.getByText(/Approve \(2\)/));
    await screen.findByText(/dora: approved/);
    await screen.findByText(/eve: approved/);
    expect(screen.getByRole("checkbox", { name: /Select all/i }).checked).toBe(false);
  });

  it("reports a per-id failure as text, with the code, not as raw markup", async () => {
    vi.mocked(api).mockResolvedValueOnce({ results: [{ id: "4", ok: true }, { id: "5", error: "bad_state" }] });
    renderMembers();
    fireEvent.click(screen.getByRole("checkbox", { name: /Select all/i }));
    fireEvent.click(screen.getByText(/Reject selected \(2\)/));
    fireEvent.change(screen.getByLabelText(/Reason \(optional\)/), { target: { value: "unknown device" } });
    fireEvent.click(screen.getByText(/Reject \(2\)/));
    await screen.findByText(/dora: rejected/);
    // Results stay visible even when no pending requests remain to show the section.
    const eve = await screen.findByText(/eve: not changed/);
    expect(eve.textContent).toBe("eve: not changed (bad_state)");
    expect(eve.innerHTML).not.toContain("&lt;span");
    expect(api).toHaveBeenLastCalledWith("/admin/members/bulk", expect.objectContaining({
      body: expect.objectContaining({ action: "reject", reason: "unknown device" }),
    }));
  });
});
