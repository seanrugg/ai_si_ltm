import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor, within } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";

afterEach(() => cleanup());
import { limitsText, modeLabels, rateLimitedText, settingsFormFrom, splitTokens, tokenFormProblem, default as Access } from "./Access.jsx";
import { api } from "../api/client.js";

vi.mock("../api/client.js", () => ({ api: vi.fn(async () => ({})) }));

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
  mcpData.tokens = [];
  mcpData.mode = "off";
  mcpData.open_scope = { types: [], swarms: null };
  vi.mocked(api).mockClear();
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

describe("MCP rate limits (#443)", () => {
  it("shows the per-caller and shared limits", () => {
    expect(limitsText({ caller_limit: 60, open_budget: 300 }))
      .toBe("60 calls/min per caller; all tokenless callers together: 300 calls/min");
    expect(limitsText({ caller_limit: 60, open_budget: 0 })).toBe("60 calls/min per caller; no shared limit for tokenless callers");
  });
  it("sums the last 24 h of rate-limited calls by reason", () => {
    expect(rateLimitedText({ total: 0, by_reason: { per_caller: 0, open_budget: 0 } })).toBe("None");
    expect(rateLimitedText({ total: 130, by_reason: { per_caller: 100, open_budget: 30 } }))
      .toBe("130 (per caller: 100, shared open limit: 30)");
    expect(rateLimitedText(undefined)).toBeNull();
  });
});

const tok = (id, name, extra = {}) => ({
  id, name, scope: { types: ["event"] }, use_count: 0, stale: false, created_at: "2026-11-01T00:00:00Z",
  expires_at: null, revoked_at: null, last_used_at: null, state: "active", reads_nothing: false, ...extra,
});

describe("MCP tokens (#445)", () => {
  it("splits active tokens from revoked and expired ones", () => {
    const { active, inactive } = splitTokens([tok("1", "a"), tok("2", "b", { state: "revoked" }),
      tok("3", "c", { state: "expired" })]);
    expect(active.map((t) => t.name)).toEqual(["a"]);
    expect(inactive.map((t) => t.name)).toEqual(["b", "c"]);
  });

  it("won't send a token without a record type", () => {
    const form = { name: "reader", types: "", agents: "", swarms: "echo-base" };
    expect(tokenFormProblem(form)).toMatch(/at least one record type/);
    expect(tokenFormProblem({ ...form, types: " , " })).toMatch(/at least one record type/);
    expect(tokenFormProblem({ ...form, types: "event" })).toBeNull();
    expect(tokenFormProblem({ ...form, name: " ", types: "event" })).toMatch(/name/);
  });

  it("shows active tokens; revoked and expired are collapsed, with Delete and no Revoke", async () => {
    mcpData.tokens = [tok("1", "live"), tok("2", "old", { state: "revoked", revoked_at: "2026-11-02T00:00:00Z" }),
      tok("3", "lapsed", { state: "expired", expires_at: "2026-11-03T00:00:00Z" })];
    renderAccess();
    const list = screen.getByRole("list", { name: "MCP read tokens" });
    expect(within(list).getByText("live")).toBeTruthy();
    expect(within(list).queryByText("old")).toBeNull();
    expect(within(list).getByRole("button", { name: "Revoke" })).toBeTruthy();
    const toggle = screen.getByRole("button", { name: "Show revoked and expired (2)" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    const past = screen.getByRole("list", { name: "Revoked and expired tokens" });
    expect(within(past).queryByRole("button", { name: "Revoke" })).toBeNull();
    const deletes = within(past).getAllByRole("button", { name: "Delete" });
    expect(deletes).toHaveLength(2);
    fireEvent.click(deletes[0]);
    fireEvent.click(within(past).getByRole("button", { name: "Delete old" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/admin/mcp/tokens/2", { method: "DELETE" }));
  });

  it("flags an active token that reads nothing", () => {
    mcpData.tokens = [tok("1", "empty", { scope: { swarms: ["echo-base"], payloads: true }, reads_nothing: true })];
    renderAccess();
    expect(screen.getByText(/Reads nothing: this token names no record types/)).toBeTruthy();
  });
});

describe("New token form on the Access page (#446)", () => {
  it("creates with the form's body, then reloads the list", async () => {
    const reload = vi.fn();
    const { useApi } = await import("../api/useApi.js");
    vi.mocked(useApi).mockImplementation((path) => {
      if (path === "/admin/mcp") return { data: mcpData, error: null, loading: false, reload };
      if (path === "/admin/enrollment/policy") return { data: policyData, error: null, loading: false, reload: () => {} };
      return { data: null, error: null, loading: false, reload: () => {} };
    });
    vi.mocked(api).mockResolvedValueOnce({ id: "9", name: "reader", token: "ltms_x" });
    renderAccess();
    fireEvent.click(screen.getByRole("button", { name: "New token" }));
    fireEvent.change(screen.getByLabelText("Name (required)"), { target: { value: "reader" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "lesson" }));
    fireEvent.click(screen.getByRole("button", { name: "Create token" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/admin/mcp/tokens", { method: "POST", body: {
      name: "reader", scope: { types: ["lesson"] }, expires_at: null, owner: null, purpose: null } }));
    await waitFor(() => expect(reload).toHaveBeenCalled());
    expect(await screen.findByLabelText("New token value")).toBeTruthy();
  });
});

describe("open payloads (#447)", () => {
  it("the Open radio's text follows the setting", () => {
    expect(modeLabels(false).open[1]).toBe("Anyone can read the open scope (headers only).");
    expect(modeLabels(true).open[1]).toBe("Anyone can read the open scope (headers and payloads).");
    expect(settingsFormFrom({ mode: "open", open_scope: { types: ["event"], payloads: true } }).payloads).toBe(true);
    expect(settingsFormFrom({ mode: "open", open_scope: { types: ["event"] } }).payloads).toBe(false);
  });

  it("the checkbox appears only in Open mode", () => {
    renderAccess();
    expect(screen.queryByRole("checkbox", { name: "Include payloads" })).toBeNull();
    fireEvent.click(screen.getByRole("radio", { name: /Open/ }));
    expect(screen.getByRole("checkbox", { name: "Include payloads" }).checked).toBe(false);
  });

  it("ticking it warns, changes the wording, and saving sends open_scope.payloads with the confirm", async () => {
    mcpData.mode = "open";
    mcpData.open_scope = { types: ["event"], swarms: null };
    renderAccess();
    fireEvent.click(screen.getByRole("checkbox", { name: "Include payloads" }));
    expect(screen.getByText(/Anyone who can reach this server can read full record contents/)).toBeTruthy();
    expect(screen.getByText("Open scope (headers and payloads)")).toBeTruthy();
    expect(screen.getByText("Anyone can read the open scope (headers and payloads).")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: /payloads included/ }));  // turning it on is a widening
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/admin/mcp/settings", { method: "PUT", body: {
      mode: "open", open_scope: { types: ["event"], swarms: null, payloads: true }, confirm: "open" } }));
  });

  it("starts ticked when the server has it on", () => {
    mcpData.mode = "open";
    mcpData.open_scope = { types: ["event"], swarms: null, payloads: true };
    renderAccess();
    expect(screen.getByRole("checkbox", { name: "Include payloads" }).checked).toBe(true);
  });
});
