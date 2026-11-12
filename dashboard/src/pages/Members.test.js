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
    { id: 1, name: "alice", status: "active", kind: "agent", permissions: [], last_ack_seq: 3, last_seen_at: null,
      memory_generation: 2 },
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
    { id: 1, name: "alice", status: "active", kind: "agent", permissions: [], last_ack_seq: 3, last_seen_at: null,
      memory_generation: 2 },
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

describe("wipe memory", () => {
  it("offers Wipe memory for every active member and shows the wipe panel", () => {
    renderMembers();
    expect(screen.getAllByRole("button", { name: /^Wipe memory$/ })).toHaveLength(3);
    fireEvent.click(screen.getAllByRole("button", { name: /^Wipe memory$/ })[0]);
    expect(screen.getByText(/Clean slate\. This can't be undone\./)).toBeDefined();
    expect(screen.getByText(/Wiped 2 times/)).toBeDefined();
    expect(screen.getAllByLabelText(/About wiping memory/)).not.toHaveLength(0);
    expect(screen.getAllByLabelText(/Wipe vs delete/)).not.toHaveLength(0);
  });

  it("omits Wiped N times when the member was never wiped", () => {
    renderMembers();
    fireEvent.click(screen.getAllByRole("button", { name: /^Wipe memory$/ })[1]); // bob, never wiped
    expect(screen.queryByText(/Wiped \d+ time/)).toBeNull();
  });

  it("enables the wipe button only when the confirm input matches the member's name", () => {
    renderMembers();
    fireEvent.click(screen.getAllByRole("button", { name: /^Wipe memory$/ })[0]);
    const wipeButton = screen.getByRole("button", { name: /Wipe alice's memory/ });
    expect(wipeButton.disabled).to.equal(true);
    fireEvent.change(screen.getByLabelText(/Confirm by typing alice/), { target: { value: "alice" } });
    expect(wipeButton.disabled).to.equal(false);
  });

  it("wipes with the typed name, says so in text, and asks for the name again next time", async () => {
    renderMembers();
    fireEvent.click(screen.getAllByRole("button", { name: /^Wipe memory$/ })[0]);
    fireEvent.change(screen.getByLabelText(/Confirm by typing alice/), { target: { value: "alice" } });
    fireEvent.click(screen.getByRole("button", { name: /Wipe alice's memory/ }));
    expect(await screen.findByText("alice's memory was wiped.")).toBeDefined();
    expect(api).toHaveBeenCalledWith("/admin/members/1/wipe", { method: "POST", body: { confirm: "alice" } });

    fireEvent.click(screen.getAllByRole("button", { name: /^Wipe memory$/ })[0]);
    expect(screen.getByLabelText(/Confirm by typing alice/).value).toBe("");
    expect(screen.getByRole("button", { name: /Wipe alice's memory/ }).disabled).toBe(true);
  });

  it("clears the typed name on Cancel", () => {
    renderMembers();
    fireEvent.click(screen.getAllByRole("button", { name: /^Wipe memory$/ })[0]);
    fireEvent.change(screen.getByLabelText(/Confirm by typing alice/), { target: { value: "alice" } });
    fireEvent.click(screen.getByRole("button", { name: /^Cancel$/ }));
    fireEvent.click(screen.getAllByRole("button", { name: /^Wipe memory$/ })[0]);
    expect(screen.getByRole("button", { name: /Wipe alice's memory/ }).disabled).toBe(true);
  });

  it("links to Export from the wipe panel", () => {
    renderMembers();
    fireEvent.click(screen.getAllByRole("button", { name: /^Wipe memory$/ })[0]);
    expect(screen.getByRole("link", { name: "Export" }).getAttribute("href")).toBe("/export");
  });
});

describe("intended swarm and role on the pending card (#295)", () => {
  it("shows what the device is for, and the hub note for a would-be hub", () => {
    allMembers = [
      { id: "7", name: "hubby", status: "pending", kind: "relay", permissions: [], requested_permissions: ["contribute"],
        public_key: "cccc3333cccc3333cccc3333cccc3333cccc3333cccc3333cccc3333cccc3333", created_at: "2026-01-01T00:00:00Z",
        intended: { swarm: "echo-base", role: "hub" } },
      { id: "8", name: "plain", status: "pending", kind: "robot", permissions: [], requested_permissions: ["contribute"],
        public_key: "dddd4444dddd4444dddd4444dddd4444dddd4444dddd4444dddd4444dddd4444", created_at: "2026-01-01T00:00:00Z" },
    ];
    const { container } = renderMembers();
    expect(container.textContent).toContain("Wants to be the hub of echo-base");
    expect(container.textContent).toContain("Relay rights come from the swarm's root key");
    expect(container.textContent.match(/Wants to be the/g)).toHaveLength(1);
  });
});

describe("approval card fingerprint check (#283)", () => {
  it("labels the 16 characters as the start of the public key and gives the safe command", () => {
    const { container } = renderMembers();
    expect(screen.getAllByText("Public key (first 16 characters)").length).toBe(2);
    expect(container.textContent).toContain("python -m cli_agent pubkey --key");
    expect(container.textContent).toContain("Don't open the key file itself: it contains the private key.");
    expect(container.textContent).not.toContain("against the key file on the device");
  });
});

describe("kind and permissions are labelled separately (#280)", () => {
  it("shows Kind and Permissions as two labelled parts, with the profile's kind wording", () => {
    allMembers = [
      { id: 9, name: "hub-one", status: "active", kind: "relay", permissions: ["contribute", "relay"],
        last_ack_seq: 0, last_seen_at: null },
      { id: 10, name: "robo", status: "active", kind: "robot", permissions: [], last_ack_seq: 0, last_seen_at: null,
        cognition: { value: "offboard" } },
    ];
    const { container } = renderMembers();
    const text = container.textContent;
    expect(text).toContain("Kind: Hub · Permissions: contribute, relay");
    expect(text).toContain("Kind: Hybrid · Permissions: none");
    expect(text).not.toContain("relay, contribute, relay");
  });
});

describe("member row links (#279)", () => {
  it("the name opens the profile and a separate link opens the filtered timeline", () => {
    renderMembers();
    const name = screen.getByRole("link", { name: "Profile of alice" });
    expect(name.textContent).toBe("alice");
    expect(name.getAttribute("href")).toBe("/members/alice/profile");
    const timeline = screen.getByRole("link", { name: "Timeline of alice" });
    expect(timeline.textContent).toBe("Timeline");
    expect(timeline.getAttribute("href")).toBe("/?agent=alice");
  });
});

describe("proxied members (#311)", () => {
  it("badges a proxied member and names who holds its key on the pending card", () => {
    allMembers = [
      { id: 11, name: "vector", status: "active", kind: "robot", permissions: ["contribute"], last_ack_seq: 0,
        last_seen_at: null, proxied_by: "sidecar on hub-host" },
      { id: "12", name: "cozmo", status: "pending", kind: "robot", permissions: [], requested_permissions: ["contribute"],
        public_key: "eeee5555eeee5555eeee5555eeee5555eeee5555eeee5555eeee5555eeee5555", created_at: "2026-01-01T00:00:00Z",
        proxied_by: "sidecar on hub-host" },
    ];
    const { container } = renderMembers();
    expect(screen.getAllByText("Proxied").length).toBeGreaterThanOrEqual(2);
    expect(container.textContent).toContain("Signed for it by sidecar on hub-host, not by the device itself.");
    expect(container.textContent).toContain("Its key is held by sidecar on hub-host");
  });
});
