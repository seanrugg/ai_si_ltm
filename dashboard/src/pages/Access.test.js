import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";

afterEach(() => cleanup());
import { settingsFormFrom, default as Access } from "./Access.jsx";

const mcpData = {
  mode: "off",
  open_scope: { types: [], swarms: null },
  source: "dashboard",
  locked: false,
  updated_at: null,
  updated_by: null,
  tokens: [],
};

let policyData;
vi.mock("../api/useApi.js", () => ({
  useApi: vi.fn((path) => {
    if (path === "/admin/mcp") return { data: mcpData, error: null, loading: false, reload: () => {} };
    if (path === "/admin/enrollment/policy")
      return { data: policyData, error: null, loading: false, reload: () => {} };
    return { data: null, error: null, loading: false, reload: () => {} };
  }),
}));

beforeEach(() => {
  policyData = {
    policy: "manual",
    source: "dashboard",
    locked: false,
    updated_at: "2026-01-01T00:00:00Z",
    updated_by: "admin:sean",
    refusals: { count: 3, since: "2026-01-01T00:00:00Z" },
  };
});

function renderAccess() {
  return render(React.createElement(MemoryRouter, null, React.createElement(Access)));
}

describe("access settings form", () => {
  it("starts from the server's effective mode and scope, not a default", () => {
    const f = settingsFormFrom({ mode: "open", open_scope: { types: ["thought", "lesson"], swarms: null } });
    expect(f.mode).toBe("open");
    expect([...f.types].sort()).toEqual(["lesson", "thought"]);
    expect(f.swarms).toBe("");
  });
  it("lists named swarms and drops the free-agents null", () => {
    const f = settingsFormFrom({ mode: "token", open_scope: { types: [], swarms: ["echo-base", null] } });
    expect(f.swarms).toBe("echo-base");
  });
  it("falls back to off only when nothing has loaded", () => {
    expect(settingsFormFrom(undefined).mode).toBe("off");
  });
});

describe("enrollment policy", () => {
  it("renders the fieldset with three labeled radios and one-line descriptions", () => {
    renderAccess();
    expect(screen.getByText("Policy"));
    expect(screen.getByRole("radio", { name: /Manual/i }).type).toBe("radio");
    expect(screen.getByRole("radio", { name: /Auto-deny/i }).type).toBe("radio");
    expect(screen.getByRole("radio", { name: /Auto-approve/i }).type).toBe("radio");
    expect(screen.getByText(/Requests wait for an admin to approve or reject/));
    expect(screen.getByText(/refused at once/));
    expect(screen.getByText(/approved at once with recall only/));
  });

  it("shows the persistent warning while auto-approve is on", () => {
    policyData = { ...policyData, policy: "auto_approve" };
    renderAccess();
    expect(screen.getByText(/Anyone on the internet can join and read/));
  });

  it("does not show the warning under manual", () => {
    renderAccess();
    expect(screen.queryByText(/Anyone on the internet can join and read/)).toBeNull();
  });

  it("shows the refusals counter under auto-deny", () => {
    policyData = { ...policyData, policy: "auto_deny" };
    renderAccess();
    expect(screen.getByText(/3 requests refused since/));
  });

  it("shows source, last changed and lock notices", () => {
    renderAccess();
    expect(screen.getAllByText(/Source/).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/by admin:sean at/));
  });

  it("disables the policy radios when locked", () => {
    policyData = { ...policyData, locked: true };
    renderAccess();
    expect(screen.getByRole("radio", { name: /Manual/i }).closest("fieldset").disabled).toBe(true);
    expect(screen.getByRole("radio", { name: /Auto-approve/i }).closest("fieldset").disabled).toBe(true);
  });
});
