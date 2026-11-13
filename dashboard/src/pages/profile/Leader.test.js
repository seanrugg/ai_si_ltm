import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { AppointLeader } from "./Manage.jsx";
import { ROLES } from "../../lib/visual.js";
import { encodeKey, hexToBytes } from "../../lib/swarmKey.js";
import vectors from "../../lib/swarm-key-vectors.json";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const v = vectors.leader_appointments[0];
const swarm = { id: v.statement.swarm, name: "echo-base", leader: null,
  members: [{ name: "jetson-hub", role: "hub" }, { name: "z3-r0", role: "member" }] };
const members = [
  { name: "jetson-hub", status: "active", public_key: "a".repeat(64) },
  { name: "z3-r0", status: "active", public_key: "b".repeat(64) },
  { name: "claude", status: "active", public_key: v.statement.leader_key },
];

describe("Appoint leader (#309)", () => {
  it("offers any active member except the hub, and clears the key field", async () => {
    // The real api() is called through act; stub fetch so nothing reaches the network.
    const fetch = vi.fn(async () => new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetch);
    const act = vi.fn(async (fn) => fn());
    render(React.createElement(AppointLeader, { swarm, allMembers: members, busy: false, act }));
    const options = [...screen.getByLabelText("Leader").querySelectorAll("option")].map((o) => o.value);
    expect(options).toEqual(["", "z3-r0", "claude"]);
    expect(screen.getByText("No leader.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Leader"), { target: { value: "claude" } });
    const key = screen.getByLabelText(/Root key/);
    expect(key.type).toBe("password");
    fireEvent.change(key, { target: { value: encodeKey(hexToBytes(v.root_seed_hex)) } });
    fireEvent.click(screen.getByRole("button", { name: "Sign and appoint" }));
    await waitFor(() => expect(act).toHaveBeenCalled());
    expect(key.value).toBe("");
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(String(fetch.mock.calls[0][0])).toContain("/admin/swarms/echo-base/leader");
  });

  it("removes the leader only after confirming", () => {
    const act = vi.fn(async () => {});
    render(React.createElement(AppointLeader, { swarm: { ...swarm, leader: "claude" }, allMembers: members, busy: false, act }));
    expect(screen.getByText("Leader: claude.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Remove leader…" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove leader" }));
    expect(act).toHaveBeenCalledTimes(1);
  });

  it("gives the leader its own legend colour", () => {
    expect(ROLES.leader.label).toBe("Leader");
    expect(new Set(Object.values(ROLES).map((r) => r.color)).size).toBe(Object.keys(ROLES).length);
  });
});
